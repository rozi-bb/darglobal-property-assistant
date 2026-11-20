"""Endpoint chat + fungsi streaming yang dipakai bersama oleh API dan UI.

Alur (canvas langkah 13): [cek login: di-skip] -> [cek usage: di-skip] -> panggil agent -> stream jawaban.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessageChunk, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field

from server.runtime import get_graph

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# Node yang token-nya boleh dikirim ke user (bukan node internal / tool).
ANSWER_NODES = {"call_model"}

ERROR_MESSAGE = "Sorry, the assistant is temporarily unavailable. Please try again in a moment."


def _chunk_text(chunk: AIMessageChunk) -> str:
    """Ambil teks dari chunk (content bisa string atau list blok)."""
    content = chunk.content
    if isinstance(content, str):
        return content
    return "".join(
        part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"
    )


async def stream_reply(graph: CompiledStateGraph, thread_id: str, message: str) -> AsyncIterator[str]:
    """Kirim pesan user ke agent dan hasilkan potongan teks jawaban satu per satu."""
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    async for chunk, metadata in graph.astream(
        {"messages": [HumanMessage(message)]}, config, stream_mode="messages"
    ):
        if not isinstance(chunk, AIMessageChunk):
            continue
        if metadata.get("langgraph_node") not in ANSWER_NODES:
            continue
        text = _chunk_text(chunk)
        if text:
            yield text


class ChatRequest(BaseModel):
    """Body request `/api/chat`."""

    message: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    """Chat dengan streaming (Server-Sent Events).

    Event: `start` {thread_id} -> `token` {text} ... -> (`error` {message}) -> `end` {}.
    Kirim `thread_id` yang sama untuk melanjutkan percakapan.
    """
    graph = get_graph()
    thread_id = req.thread_id or uuid4().hex

    async def events() -> AsyncIterator[str]:
        yield _sse("start", {"thread_id": thread_id})
        try:
            async for text in stream_reply(graph, thread_id, req.message):
                yield _sse("token", {"text": text})
        except Exception:
            logger.exception("Chat gagal (thread_id=%s)", thread_id)
            yield _sse("error", {"message": ERROR_MESSAGE})
        yield _sse("end", {})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Thread-Id": thread_id},
    )
