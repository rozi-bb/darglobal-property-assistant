"""Akses data Postgres untuk tools (DB yang sama dengan memori chat: `CHECKPOINT_DATABASE_URL`).

Pool async dibuat sekali saat pertama dipakai (per event loop), jadi jalan di FastAPI maupun `langgraph dev`.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

_pool: AsyncConnectionPool | None = None
_pool_loop: asyncio.AbstractEventLoop | None = None


class DataUnavailableError(RuntimeError):
    """Database data tidak bisa dipakai (env kosong / tabel belum dimuat)."""


def database_url() -> str:
    """URL Postgres dari env. Error kalau kosong."""
    load_dotenv(override=False)
    url = os.environ.get("CHECKPOINT_DATABASE_URL", "").strip()
    if not url:
        raise DataUnavailableError("CHECKPOINT_DATABASE_URL is empty: property data is not available.")
    return url


async def get_pool() -> AsyncConnectionPool:
    """Pool koneksi (dibuat ulang kalau event loop berganti, misal antar test)."""
    global _pool, _pool_loop
    loop = asyncio.get_running_loop()
    if _pool is None or _pool_loop is not loop:
        pool = AsyncConnectionPool(
            conninfo=database_url(),
            min_size=1,
            max_size=5,
            open=False,
            kwargs={"autocommit": True, "row_factory": dict_row},
        )
        await pool.open(wait=True, timeout=10)
        _pool, _pool_loop = pool, loop
    return _pool


async def fetch_all(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Jalankan query SELECT, kembalikan list dict."""
    pool = await get_pool()
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(sql, params or {})  # type: ignore[arg-type]
        return list(await cur.fetchall())


async def close_pool() -> None:
    """Tutup pool (dipanggil saat FastAPI shutdown)."""
    global _pool, _pool_loop
    if _pool is not None:
        await _pool.close()
    _pool, _pool_loop = None, None
