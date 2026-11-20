"""Muat `data/processed/*.jsonl` ke Postgres (tabel dibuat ulang isinya tiap jalan; idempoten).

Pakai DB yang sama dengan memori chat: env `CHECKPOINT_DATABASE_URL`.
Jalankan: `docker compose up -d db` lalu `uv run python -m dataprep.load_db`
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

from dataprep.common import PROCESSED_DIR, setup_logging

log = logging.getLogger("load_db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS darglobal_projects (
    slug            text PRIMARY KEY,
    url             text NOT NULL,
    title           text NOT NULL,
    location        text,
    country         text,
    starting_price  text,
    property_type   text,
    status          text,
    units           text,
    completion      text,
    area            text,
    description     text,
    details         jsonb NOT NULL DEFAULT '{}',
    amenities       text[] NOT NULL DEFAULT '{}',
    key_features    text[] NOT NULL DEFAULT '{}',
    lat             double precision,
    lon             double precision,
    brochure_url    text,
    image_url       text,
    disclaimer      text,
    captured_at     date NOT NULL,
    source          text NOT NULL DEFAULT 'DarGlobal'
);

CREATE TABLE IF NOT EXISTS darglobal_chunks (
    chunk_id     text PRIMARY KEY,
    url          text NOT NULL,
    kind         text NOT NULL,
    title        text NOT NULL,
    section      text NOT NULL,
    text         text NOT NULL,
    captured_at  date NOT NULL,
    source       text NOT NULL DEFAULT 'DarGlobal',
    tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || text)) STORED
);
CREATE INDEX IF NOT EXISTS darglobal_chunks_tsv_idx ON darglobal_chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS darglobal_chunks_kind_idx ON darglobal_chunks (kind);

CREATE TABLE IF NOT EXISTS wasalt_listings (
    id               bigint PRIMARY KEY,
    purpose          text NOT NULL CHECK (purpose IN ('sale', 'rent')),
    title            text NOT NULL,
    property_type    text NOT NULL,
    main_type        text,
    city             text NOT NULL,
    district         text,
    address          text,
    price_sar        double precision NOT NULL,
    rent_period      text,
    rent_yearly_sar  double precision,
    bedrooms         integer,
    bathrooms        integer,
    area_sqm         double precision,
    price_per_sqm    double precision,
    age_years        integer,
    furnishing       text,
    amenities        text[] NOT NULL DEFAULT '{}',
    lat              double precision,
    lon              double precision,
    published_at     timestamptz,
    url              text NOT NULL,
    snapshot_date    date NOT NULL,
    source           text NOT NULL DEFAULT 'Wasalt'
);
-- is_active dihapus (cek sitemap tidak andal); buang juga dari tabel lama.
ALTER TABLE wasalt_listings DROP COLUMN IF EXISTS is_active;
CREATE INDEX IF NOT EXISTS wasalt_listings_filter_idx ON wasalt_listings (city, purpose, property_type);
CREATE INDEX IF NOT EXISTS wasalt_listings_price_idx ON wasalt_listings (price_sar);
"""

TABLES: dict[str, list[str]] = {
    "darglobal_projects": [
        "slug", "url", "title", "location", "country", "starting_price", "property_type", "status", "units",
        "completion", "area", "description", "details", "amenities", "key_features", "lat", "lon",
        "brochure_url", "image_url", "disclaimer", "captured_at", "source",
    ],
    "darglobal_chunks": ["chunk_id", "url", "kind", "title", "section", "text", "captured_at", "source"],
    "wasalt_listings": [
        "id", "purpose", "title", "property_type", "main_type", "city", "district", "address", "price_sar",
        "rent_period", "rent_yearly_sar", "bedrooms", "bathrooms", "area_sqm", "price_per_sqm", "age_years",
        "furnishing", "amenities", "lat", "lon", "published_at", "url", "snapshot_date", "source",
    ],
}
FILES = {
    "darglobal_projects": "darglobal_projects.jsonl",
    "darglobal_chunks": "darglobal_chunks.jsonl",
    "wasalt_listings": "wasalt_listings.jsonl",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Baca file JSONL."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]


def to_params(row: dict[str, Any], cols: list[str]) -> list[Any]:
    """Baris JSON → parameter SQL (dict → jsonb)."""
    return [Jsonb(row[c]) if isinstance(row.get(c), dict) else row.get(c) for c in cols]


def load(conn: psycopg.Connection[Any], processed_dir: Path = PROCESSED_DIR) -> dict[str, int]:
    """Buat skema lalu ganti isi tiap tabel dengan data terbaru. Kembalikan jumlah baris per tabel."""
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
        for table, cols in TABLES.items():
            rows = read_jsonl(processed_dir / FILES[table])
            cur.execute(f"TRUNCATE {table}")
            placeholders = ", ".join(["%s"] * len(cols))
            cur.executemany(
                f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
                [to_params(r, cols) for r in rows],
            )
            counts[table] = len(rows)
    conn.commit()
    return counts


def main() -> None:
    """Muat dataset bersih ke Postgres dari env `CHECKPOINT_DATABASE_URL`."""
    setup_logging()
    load_dotenv()
    url = os.environ.get("CHECKPOINT_DATABASE_URL")
    if not url:
        raise SystemExit("CHECKPOINT_DATABASE_URL belum di-set (lihat .env.example)")
    with psycopg.connect(url) as conn:
        counts = load(conn)
    for table, n in counts.items():
        log.info("%s: %d baris", table, n)


if __name__ == "__main__":
    main()
