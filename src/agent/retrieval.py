"""Hybrid search DarGlobal: dense (pgvector + bge-small) + sparse (BM25 di memori) → RRF → rerank Jev.

- Embedding chunk dibuat offline (`python -m dataprep.embed`) dan disimpan di kolom `darglobal_chunks.embedding`.
- Index BM25 dibangun dari tabel `darglobal_chunks` saat pertama dipakai (±2.700 chunk, < 1 detik).
- Rerank Jev (TypeSafe): satu request per pasangan (query, passage), jalan paralel. Tanpa key atau error
  → pakai urutan RRF (pencarian tetap jalan).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from fastembed import TextEmbedding
from rank_bm25 import BM25Okapi
from typesafe_sdk import AsyncTypeSafeClient, Noul, NoulCriteria

from agent.db import fetch_all

logger = logging.getLogger(__name__)

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
EMBED_DIM = 384
RRF_K = 60
CANDIDATES = 50  # per retriever, sebelum RRF
SHORTLIST = 20  # hasil RRF yang dikirim ke rerank
TOP_K = 4
MIN_RERANK_SCORE = 0.5
RERANK_CONCURRENCY = 8
RERANK_TIMEOUT_S = 15.0
PASSAGE_CHARS = 1800

TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    "a an and are as at be by for from has have how i in is it its of on or that the this to was what when "
    "where which who will with you your do does can about me tell".split()
)

RELEVANCE = Noul(
    instructions="Does `passage` contain information that helps answer `query` about DarGlobal?",
    criteria=NoulCriteria(
        true="The passage states facts that directly answer or clearly support an answer to the query.",
        false="The passage is off-topic or only loosely related; it does not help answer the query.",
    ),
)


@dataclass(frozen=True)
class Chunk:
    """Satu potongan teks halaman DarGlobal."""

    chunk_id: str
    url: str
    kind: str
    title: str
    section: str
    text: str
    country: str | None = None
    captured_at: str | None = None


# ---------- embedding ----------


@lru_cache(maxsize=1)
def get_embedder() -> TextEmbedding:
    """Model bge-small (ONNX, lokal). Cache model di `FASTEMBED_CACHE_PATH` kalau diisi (image Docker)."""
    return TextEmbedding(EMBED_MODEL, cache_dir=os.environ.get("FASTEMBED_CACHE_PATH") or None)


def embed_passages(texts: Sequence[str]) -> list[list[float]]:
    """Embed teks chunk (mode passage bge)."""
    return [v.tolist() for v in get_embedder().passage_embed(list(texts), batch_size=64)]


def embed_query(text: str) -> list[float]:
    """Embed query (mode query bge)."""
    return next(iter(get_embedder().query_embed(text))).tolist()


def vector_literal(vec: Sequence[float]) -> str:
    """Format teks pgvector: '[0.1,0.2,...]' (dipakai dengan cast `::vector`)."""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


# ---------- sparse (BM25) ----------


def tokenize(text: str) -> list[str]:
    """Token BM25: huruf kecil, alfanumerik, tanpa stopword."""
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in STOPWORDS]


def body_key(text: str) -> str:
    """Kunci isi chunk tanpa baris judul, untuk membuang chunk dengan isi sama (seksi AIDA dipakai bersama)."""
    body = "\n".join(text.split("\n")[1:]).strip().lower()
    return hashlib.sha1(" ".join(body.split()).encode()).hexdigest()


class HybridIndex:
    """Chunk di memori + index BM25."""

    def __init__(self, chunks: Sequence[Chunk]) -> None:
        """Bangun index BM25 dari chunk (judul + seksi + isi)."""
        self.chunks = list(chunks)
        self.by_id = {c.chunk_id: c for c in self.chunks}
        corpus = [tokenize(f"{c.title} {c.section} {c.text}") for c in self.chunks]
        self.bm25 = BM25Okapi(corpus) if corpus else None

    def allowed_ids(self, country: str | None) -> set[str] | None:
        """Chunk yang lolos filter negara (None = tanpa filter)."""
        if not country:
            return None
        return {c.chunk_id for c in self.chunks if matches_country(c, country)}

    def bm25_ranking(self, query: str, allowed: set[str] | None = None, k: int = CANDIDATES) -> list[str]:
        """Urutan chunk_id menurut skor BM25 (hanya skor > 0)."""
        tokens = tokenize(query)
        if not tokens or self.bm25 is None:
            return []
        scores = self.bm25.get_scores(tokens)
        ranked = sorted(range(len(self.chunks)), key=lambda i: -scores[i])
        out: list[str] = []
        for i in ranked:
            if scores[i] <= 0:
                break
            cid = self.chunks[i].chunk_id
            if allowed is None or cid in allowed:
                out.append(cid)
                if len(out) >= k:
                    break
        return out


def matches_country(chunk: Chunk, country: str) -> bool:
    """Halaman proyek: negara dari tabel proyek. Blog/press/umum: negara disebut di teks."""
    c = country.strip().lower()
    if chunk.country:
        return chunk.country.lower() == c
    if chunk.kind == "project":
        return False
    names = {c, *COUNTRY_ALIASES.get(c, ())}
    text = f"{chunk.title} {chunk.text}".lower()
    return any(n in text for n in names)


COUNTRY_ALIASES: dict[str, tuple[str, ...]] = {
    "saudi arabia": ("saudi", "ksa", "riyadh", "jeddah"),
    "united arab emirates": ("uae", "dubai", "abu dhabi"),
    "united kingdom": ("london", "uk"),
    "oman": ("muscat", "aida"),
    "qatar": ("doha",),
    "spain": ("marbella", "costa del sol"),
}


# ---------- fusion ----------


def rrf(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: skor = Σ 1 / (k + rank)."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking, start=1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def dedupe(ids: Sequence[str], by_id: dict[str, Chunk]) -> list[str]:
    """Buang chunk dengan isi sama, pertahankan urutan."""
    seen: set[str] = set()
    out: list[str] = []
    for cid in ids:
        key = body_key(by_id[cid].text)
        if key not in seen:
            seen.add(key)
            out.append(cid)
    return out


# ---------- rerank (Jev) ----------


def rerank_state(query: str, chunk: Chunk) -> dict[str, Any]:
    """State Jev: query + satu passage (pertanyaan relevansi tentang pasangan ini)."""
    return {"query": query, "passage": {"title": chunk.title, "section": chunk.section,
                                        "text": chunk.text[:PASSAGE_CHARS]}}


async def rerank(query: str, chunks: Sequence[Chunk]) -> list[float] | None:
    """Skor relevansi 0–1 per chunk dari Jev. None kalau key kosong atau API error (→ fallback RRF)."""
    api_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not api_key or not chunks:
        return None
    sem = asyncio.Semaphore(RERANK_CONCURRENCY)
    try:
        async with AsyncTypeSafeClient(api_key=api_key, timeout=RERANK_TIMEOUT_S) as client:

            async def score(chunk: Chunk) -> float:
                async with sem:
                    resp = await client.system_one(state=rerank_state(query, chunk),
                                                   questions={"relevant": RELEVANCE})
                    return float(resp.answers["relevant"].noul)

            return list(await asyncio.gather(*(score(c) for c in chunks)))
    except Exception:
        logger.warning("Rerank Jev gagal, pakai urutan RRF.", exc_info=True)
        return None


def select_top(
    candidates: Sequence[Chunk], scores: Sequence[float] | None, k: int = TOP_K, min_score: float = MIN_RERANK_SCORE
) -> list[tuple[Chunk, float | None]]:
    """Tanpa skor rerank: top-k urutan RRF. Dengan skor: urut skor, ambil yang ≥ ambang."""
    if scores is None:
        return [(c, None) for c in candidates[:k]]
    ranked = sorted(zip(candidates, scores, strict=True), key=lambda cs: -cs[1])
    return [(c, s) for c, s in ranked if s >= min_score][:k]


# ---------- load + search ----------

_index: HybridIndex | None = None


async def load_index(force: bool = False) -> HybridIndex:
    """Bangun index dari Postgres (sekali per proses)."""
    global _index
    if _index is None or force:
        rows = await fetch_all(
            "SELECT c.chunk_id, c.url, c.kind, c.title, c.section, c.text, c.captured_at::text AS captured_at, "
            "p.country FROM darglobal_chunks c LEFT JOIN darglobal_projects p ON p.url = c.url ORDER BY c.chunk_id"
        )
        _index = HybridIndex([Chunk(**r) for r in rows])
        logger.info("Index BM25 DarGlobal siap: %d chunk.", len(_index.chunks))
    return _index


async def dense_ranking(query: str, allowed: set[str] | None, k: int = CANDIDATES) -> list[str]:
    """Urutan chunk_id menurut jarak cosine embedding (pgvector)."""
    vec = await asyncio.to_thread(embed_query, query)
    rows = await fetch_all(
        "SELECT chunk_id FROM darglobal_chunks WHERE embedding IS NOT NULL "
        "ORDER BY embedding <=> %(v)s::vector LIMIT %(k)s",
        {"v": vector_literal(vec), "k": k * 4 if allowed is not None else k},
    )
    ids = [r["chunk_id"] for r in rows]
    if allowed is not None:
        ids = [i for i in ids if i in allowed]
    return ids[:k]


@dataclass
class SearchResult:
    """Hasil hybrid search: (chunk, skor rerank atau None) + apakah rerank Jev dipakai."""

    hits: list[tuple[Chunk, float | None]]
    reranked: bool


async def hybrid_search(query: str, country: str | None = None) -> SearchResult:
    """Cari chunk DarGlobal paling relevan (≤ 4)."""
    index = await load_index()
    allowed = index.allowed_ids(country)
    dense = await dense_ranking(query, allowed)
    sparse = index.bm25_ranking(query, allowed)
    fused = [cid for cid, _ in rrf([dense, sparse])]
    shortlist = [index.by_id[cid] for cid in dedupe(fused, index.by_id)[:SHORTLIST]]
    scores = await rerank(query, shortlist)
    return SearchResult(hits=select_top(shortlist, scores), reranked=scores is not None)


async def warm_up() -> None:
    """Muat model embedding + index BM25 saat app start (request pertama tidak lambat)."""
    await asyncio.to_thread(get_embedder)
    await load_index()
