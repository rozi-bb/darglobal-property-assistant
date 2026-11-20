"""Embed `darglobal_chunks` dengan bge-small (lokal, tanpa API) ke kolom pgvector `embedding`.

Jalankan setelah `dataprep.load_db` (load ulang mengosongkan tabel chunk):
`uv run python -m dataprep.embed` (tambah `--all` untuk embed ulang semua chunk).
"""

from __future__ import annotations

import argparse
import logging
import os

import psycopg
from dotenv import load_dotenv

from agent.retrieval import EMBED_DIM, embed_passages, vector_literal
from dataprep.common import setup_logging

log = logging.getLogger("embed")

SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;
ALTER TABLE darglobal_chunks ADD COLUMN IF NOT EXISTS embedding vector({EMBED_DIM});
"""
BATCH = 128


def passage_text(title: str, section: str, text: str) -> str:
    """Teks yang di-embed: judul + seksi + isi (judul sering hanya ada di baris pertama)."""
    return f"{title} — {section}\n{text}"


def main() -> None:
    """Tambah kolom embedding (kalau belum ada) lalu embed chunk per batch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="embed ulang semua chunk")
    args = parser.parse_args()
    setup_logging()
    load_dotenv(override=False)
    url = os.environ.get("CHECKPOINT_DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("CHECKPOINT_DATABASE_URL kosong.")

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(SCHEMA)
        where = "" if args.all else "WHERE embedding IS NULL"
        rows = conn.execute(f"SELECT chunk_id, title, section, text FROM darglobal_chunks {where}").fetchall()
        log.info("Embed %d chunk (model bge-small)...", len(rows))
        for start in range(0, len(rows), BATCH):
            batch = rows[start : start + BATCH]
            vectors = embed_passages([passage_text(t, s, x) for _, t, s, x in batch])
            with conn.cursor() as cur:
                cur.executemany(
                    "UPDATE darglobal_chunks SET embedding = %s::vector WHERE chunk_id = %s",
                    [(vector_literal(v), cid) for (cid, *_), v in zip(batch, vectors, strict=True)],
                )
            log.info("  %d/%d", min(start + BATCH, len(rows)), len(rows))
        missing = conn.execute("SELECT count(*) FROM darglobal_chunks WHERE embedding IS NULL").fetchone()
        log.info("Selesai. Chunk tanpa embedding: %s", missing[0] if missing else "?")


if __name__ == "__main__":
    main()
