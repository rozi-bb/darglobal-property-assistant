"""Kerangka backend FastAPI.

- `/health`    : cek server hidup
- `/api/chat`  : endpoint chat streaming (server/chat.py)
- `/chat`      : UI Chainlit (server/ui.py), `/` diarahkan ke sini

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
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from agent.graph import build_graph
from server.chat import router as chat_router
from server.config import get_settings
from server.runtime import set_graph

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

UI_PATH = "/chat"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Siapkan checkpointer + graph saat start, tutup koneksi saat stop."""
    settings = get_settings()

    if not settings.database_url:
        logger.warning("CHECKPOINT_DATABASE_URL kosong: memori chat disimpan di RAM (hilang saat restart).")
        set_graph(build_graph(checkpointer=InMemorySaver()))
        yield
        set_graph(None)
        return

    async with AsyncConnectionPool(
        conninfo=settings.database_url,
        max_size=10,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    ) as pool:
        checkpointer = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
        await checkpointer.setup()  # buat tabel checkpointer kalau belum ada
        set_graph(build_graph(checkpointer=checkpointer))
        logger.info("Checkpointer Postgres siap.")
        yield
        set_graph(None)


app = FastAPI(title=get_settings().app_name, lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    """Cek server hidup."""
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """Arahkan ke UI chat."""
    return RedirectResponse(UI_PATH)


app.include_router(chat_router)

# UI Chainlit di-mount terakhir supaya route di atas tidak tertutup.
mount_chainlit(app=app, target=str(Path(__file__).with_name("ui.py")), path=UI_PATH)
