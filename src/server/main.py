"""Kerangka backend FastAPI.

- `/health`    : cek server hidup
- `/api/chat`  : endpoint chat streaming (server/chat.py)
- `/chat`      : UI Chainlit (server/ui.py), `/` diarahkan ke sini
- `/case-study/`: halaman case study statis (docs/case-study, ikut di image Docker)

Jalankan lokal: `uv run uvicorn server.main:app --reload --port 8000`
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from chainlit.utils import mount_chainlit
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from agent import db, retrieval
from server.chat import router as chat_router
from server.config import get_settings
from server.runtime import set_checkpointer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

UI_PATH = "/chat"
CASE_STUDY_PATH = "/case-study"
# src/server/main.py → <root>/docs/case-study (sama di repo lokal dan di image: /app/docs/case-study)
CASE_STUDY_DIR = Path(__file__).resolve().parents[2] / "docs" / "case-study"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Siapkan checkpointer + graph saat start, tutup koneksi saat stop."""
    settings = get_settings()

    if not settings.database_url:
        logger.warning("CHECKPOINT_DATABASE_URL kosong: memori chat di RAM, data properti tidak tersedia.")
        set_checkpointer(InMemorySaver())
        yield
        set_checkpointer(None)
        return

    async with AsyncConnectionPool(
        conninfo=settings.database_url,
        max_size=10,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    ) as pool:
        checkpointer = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
        await checkpointer.setup()  # buat tabel checkpointer kalau belum ada
        set_checkpointer(checkpointer)
        logger.info("Checkpointer Postgres siap.")
        try:
            await retrieval.warm_up()  # model embedding + index BM25, supaya request pertama tidak lambat
        except Exception:
            logger.exception("Warm-up pencarian DarGlobal gagal (cek tabel darglobal_chunks / dataprep.embed).")
        yield
        set_checkpointer(None)
        await db.close_pool()


app = FastAPI(title=get_settings().app_name, lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    """Cek server hidup."""
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """Arahkan ke UI chat."""
    return RedirectResponse(UI_PATH)


@app.get(CASE_STUDY_PATH, include_in_schema=False)
async def case_study() -> RedirectResponse:
    """Tambah garis miring supaya gambar relatif di halaman case study ter-resolve."""
    return RedirectResponse(f"{CASE_STUDY_PATH}/")


app.include_router(chat_router)

if CASE_STUDY_DIR.is_dir():
    app.mount(CASE_STUDY_PATH, StaticFiles(directory=CASE_STUDY_DIR, html=True), name="case-study")

# UI Chainlit di-mount terakhir supaya route di atas tidak tertutup.
mount_chainlit(app=app, target=str(Path(__file__).with_name("ui.py")), path=UI_PATH)
