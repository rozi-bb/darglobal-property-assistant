"""Bangun dataset bersih dari data mentah → `data/processed/`.

Output:
- `darglobal_projects.jsonl`: proyek DarGlobal terstruktur (tabel `darglobal_projects`).
- `darglobal_chunks.jsonl`: potongan dokumen DarGlobal untuk RAG (tabel `darglobal_chunks`).
- `wasalt_listings.jsonl`: listing Wasalt bersih (tabel `wasalt_listings`); `wasalt_rejected.jsonl`: yang dibuang + alasan.
- `manifest.json`: ringkasan jumlah & sumber.

Jalankan: `uv run python -m dataprep.build`
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from dataprep.clean_darglobal import (
    PATH_COUNTRY,
    DocChunk,
    Project,
    classify,
    country_index,
    details_from_text,
    load_raw_pages,
    location_from_text,
    main_holders,
    norm_country,
    old_text_clean,
    page_title,
    page_to_chunks,
    page_to_project,
    pick_detail,
    split_chunks,
)
from dataprep.clean_wasalt import clean_all
from dataprep.common import DATA_DIR, PROCESSED_DIR, RAW_DIR, setup_logging, write_json
from dataprep.darglobal import url_key

log = logging.getLogger("build")
OLD_SNAPSHOT_DATE = "2026-11-19"


def write_jsonl(path: Path, rows: Iterable[BaseModel | dict[str, Any]]) -> int:
    """Tulis JSONL; kembalikan jumlah baris."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            data = row.model_dump() if isinstance(row, BaseModel) else row
            fh.write(json.dumps(data, ensure_ascii=False) + "\n")
            n += 1
    return n


KIND_PRIORITY = {"project": 0, "press": 1, "insight": 1, "blog": 2, "general": 3}


def dedupe_chunks(chunks: list[DocChunk]) -> list[DocChunk]:
    """Buang chunk yang isinya sama persis dengan chunk lain; simpan versi dari jenis halaman paling spesifik.

    Chunk proyek selalu disimpan (seksi sama di beberapa proyek tetap relevan per proyek).
    """
    best: dict[str, DocChunk] = {}
    for c in chunks:
        body = c.text.split("\n\n", 1)[-1].strip().lower()
        prev = best.get(body)
        if prev is None or KIND_PRIORITY.get(c.kind, 9) < KIND_PRIORITY.get(prev.kind, 9):
            best[body] = c
    keep = {id(c) for c in best.values()}
    return [c for c in chunks if id(c) in keep or c.kind == "project"]


def minimal_project(raw: dict[str, Any], key: str, title: str, chunks: list[DocChunk], countries: dict[str, str]) -> Project:
    """Proyek dari template non-standar (one-of-one, landing page): judul + ringkasan dari seksi pertama."""
    first = chunks[0].text.split("\n\n", 1)[-1] if chunks else None
    body = "\n".join(c.text for c in chunks)
    location = next(
        (n["location"] for _, n in main_holders(raw["pageProps"], key) if isinstance(n, dict) and isinstance(n.get("location"), str)),
        None,
    )
    return text_project(
        key, raw["url"], title, body, first[:800] if first else None, raw["captured_at"], countries, location
    )


def text_project(
    key: str,
    url: str,
    title: str,
    body: str,
    description: str | None,
    captured_at: str,
    countries: dict[str, str],
    location: str | None = None,
) -> Project:
    """Proyek dari teks (tanpa JSON terstruktur): detail 'Label: Nilai' + lokasi 'Kota | Negara'."""
    details = details_from_text(body)
    location = location or location_from_text(body)
    return Project(
        slug=key, url=url, title=title, location=location,
        country=norm_country(countries.get(title.lower()) or location or PATH_COUNTRY.get(key.split("/")[0])),
        property_type=pick_detail(details, "property type"), status=pick_detail(details, "status"),
        units=pick_detail(details, "unit"), completion=pick_detail(details, "completion", "handover"),
        area=pick_detail(details, "area", "size"), description=description, details=details,
        captured_at=captured_at,
    )


def build_darglobal() -> dict[str, Any]:
    """Halaman Wayback (+ fallback scrape lama) → proyek & chunk."""
    pages = load_raw_pages(RAW_DIR / "darglobal" / "pages")
    countries = country_index(pages)
    country_names: set[str] = set()
    for raw in pages[:1]:
        attrs = (raw["pageProps"].get("globalData") or {}).get("attributes") or {}
        country_names = {c["name"] for c in attrs.get("CountryList") or [] if c.get("name")}

    projects: dict[str, Project] = {}
    chunks: list[DocChunk] = []
    kinds: Counter[str] = Counter()
    seen_keys: set[str] = set()
    for raw in pages:
        key = url_key(raw["url"])
        seen_keys.add(key)
        kind = classify(key, raw["pageProps"])
        kinds[kind] += 1
        if kind == "index":
            continue  # halaman daftar (blog/press) hanya ringkasan; isinya ada di halaman masing-masing
        title = page_title(raw["pageProps"], key, key)
        page_chunks = page_to_chunks(raw, kind, title, key)
        chunks += page_chunks
        if kind == "project":
            proj = page_to_project(raw, key, countries) or minimal_project(raw, key, title, page_chunks, countries)
            projects[key] = proj

    # Fallback: halaman di scrape lama (Nov 2026) yang tidak ada di arsip Wayback.
    old = json.loads((DATA_DIR / "darglobal_pages.json").read_text(encoding="utf-8"))
    fallback = 0
    for row in old:
        key = url_key(row["url"])
        # via=next_data di scrape lama hanya berisi menu global (daftar negara/proyek), bukan isi halaman.
        if key in seen_keys or not row.get("text") or row.get("via") == "next_data":
            continue
        text = old_text_clean(row["text"], country_names)
        title = (row.get("h1") or row.get("title") or key).split(" | ")[0].strip()
        kind = "project" if row["kind"] == "project" else "general"
        page_chunks = [
            DocChunk(
                chunk_id=f"old-{key.replace('/', '-')}-{i}", url=row["url"], kind=kind, title=title,
                section="Page", text=f"# {title} — Page\n\n{piece}", captured_at=OLD_SNAPSHOT_DATE,
            )
            for i, piece in enumerate(split_chunks(text))
        ]
        chunks += page_chunks
        if kind == "project":
            projects[key] = text_project(
                key, row["url"], title, text, row.get("description") or None, OLD_SNAPSHOT_DATE, countries
            )
        kinds[f"{kind} (fallback lama)"] += 1
        fallback += 1

    # URL lama & baru untuk proyek yang sama (redirect) → simpan snapshot terbaru saja.
    latest: dict[str, Project] = {}
    for proj in projects.values():
        prev = latest.get(proj.title.lower())
        if prev is None or proj.captured_at > prev.captured_at:
            latest[proj.title.lower()] = proj
    dropped_urls = {p.url for p in projects.values()} - {p.url for p in latest.values()}
    chunks = dedupe_chunks([c for c in chunks if c.url not in dropped_urls])
    n_proj = write_jsonl(PROCESSED_DIR / "darglobal_projects.jsonl", sorted(latest.values(), key=lambda p: p.slug))
    n_chunk = write_jsonl(PROCESSED_DIR / "darglobal_chunks.jsonl", chunks)
    log.info("DarGlobal: %d halaman, %d proyek, %d chunk, %d fallback", len(pages), n_proj, n_chunk, fallback)
    return {
        "pages_wayback": len(pages),
        "pages_fallback_old_snapshot": fallback,
        "pages_by_kind": dict(kinds),
        "projects": n_proj,
        "chunks": n_chunk,
        "chunks_by_kind": dict(Counter(c.kind for c in chunks)),
        "captured_at": dict(Counter(c.captured_at for c in chunks)),
    }


def build_wasalt() -> dict[str, Any]:
    """Bersihkan listing lama → listing bersih + daftar yang ditolak."""
    rows = json.loads((DATA_DIR / "wasalt_listings.json").read_text(encoding="utf-8"))
    kept, rejected = clean_all(rows)
    raw_bedrooms = {int(r["id"]): r.get("bedrooms") for r in rows}
    n = write_jsonl(PROCESSED_DIR / "wasalt_listings.jsonl", kept)
    write_jsonl(PROCESSED_DIR / "wasalt_rejected.jsonl", rejected)
    log.info("Wasalt: %d mentah → %d bersih, %d ditolak", len(rows), n, len(rejected))
    return {
        "raw": len(rows),
        "clean": n,
        "rejected": len(rejected),
        "rejected_by_reason": dict(Counter(r["reason"].split(" (")[0] for r in rejected)),
        "by_purpose": dict(Counter(i.purpose for i in kept)),
        "by_city": dict(Counter(i.city for i in kept).most_common()),
        "bedrooms_cleared": sum(1 for i in kept if i.bedrooms is None and raw_bedrooms.get(i.id)),
    }


def main() -> None:
    """Bangun semua dataset bersih dan tulis manifest."""
    setup_logging()
    manifest = {
        "darglobal": {"source": "DarGlobal (darglobal.co.uk via Wayback Machine + snapshot Nov 2026)", **build_darglobal()},
        "wasalt": {"source": "Wasalt.sa (snapshot 19–20 Nov 2026)", **build_wasalt()},
    }
    write_json(PROCESSED_DIR / "manifest.json", manifest)


if __name__ == "__main__":
    main()
