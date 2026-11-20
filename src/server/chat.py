"""Endpoint chat + fungsi `run_agent` yang dipakai bersama oleh API dan UI.

Alur (canvas langkah 13): [cek login: di-skip] -> [cek usage: di-skip] -> panggil agent -> kirim event.

Event agent:
- `StepEvent`   : agent memanggil tool (ditampilkan sebagai langkah, mis. "DarGlobal search: ...")
- `AskEvent`    : agent bertanya balik (`ask_user`); pesan user berikutnya di thread yang sama = jawabannya
- `AnswerEvent` : jawaban final, dikirim setelah lolos cek jawaban (draf yang gagal tidak pernah tampil)
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import BaseModel, Field

from agent.middleware import answer_text
from server.runtime import get_graph

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

ERROR_MESSAGE = "Sorry, the assistant is temporarily unavailable. Please try again in a moment."


@dataclass
class StepEvent:
    """Agent memanggil tool (ditampilkan sebagai langkah)."""

    tool: str
    label: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class AskEvent:
    """Agent bertanya balik dengan pilihan jawaban."""

    question: str
    options: list[str]


@dataclass
class AnswerEvent:
    """Jawaban final (sudah lolos cek jawaban)."""

    text: str


AgentEvent = StepEvent | AskEvent | AnswerEvent


def step_label(tool: str, args: dict[str, Any]) -> str:
    """Teks langkah agent untuk user (tanpa detail internal)."""
    if tool == "search_darglobal":
        where = f" ({args['country']})" if args.get("country") else ""
        return f"DarGlobal search: {args.get('query', '')}{where}"
    parts = [str(args[k]) for k in ("city", "property_type", "purpose") if args.get(k)]
    if args.get("bedrooms") is not None:
        parts.append(f"{args['bedrooms']} BR")
    if args.get("max_price"):
        parts.append(f"≤ {float(args['max_price']):,.0f} SAR")
    if tool == "search_wasalt_listings":
        return "Wasalt listings: " + " · ".join(parts)
    if tool == "wasalt_market_stats":
        return "Wasalt market statistics: " + " · ".join(parts)
    return tool


async def has_pending_question(graph: CompiledStateGraph, config: RunnableConfig) -> bool:
    """Thread sedang menunggu jawaban `ask_user`?"""
    state = await graph.aget_state(config)
    return bool(state.interrupts)


async def run_agent(graph: CompiledStateGraph, thread_id: str, message: str) -> AsyncIterator[AgentEvent]:
    """Kirim pesan user ke agent dan hasilkan event langkah / tanya balik / jawaban final."""
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    if await has_pending_question(graph, config):
        payload: Any = Command(resume=message)  # pesan user = jawaban pertanyaan `ask_user`
    else:
        payload = {"messages": [HumanMessage(message)]}

    async for update in graph.astream(payload, config, stream_mode="updates"):
        for node, data in update.items():
            if node == "__interrupt__":
                for item in data:
                    value = item.value
                    if isinstance(value, dict) and value.get("type") == "ask_user":
                        yield AskEvent(question=value["question"], options=list(value.get("options", [])))
                        return
            elif node == "model" and isinstance(data, dict):
                for msg in data.get("messages", []):
                    for call in getattr(msg, "tool_calls", None) or []:
                        if call["name"] != "ask_user":
                            yield StepEvent(call["name"], step_label(call["name"], call["args"]), call["args"])

    state = await graph.aget_state(config)
    messages = state.values.get("messages", [])
    if messages and isinstance(messages[-1], AIMessage):
        yield AnswerEvent(answer_text(messages[-1]))
    else:
        raise RuntimeError("Agent selesai tanpa jawaban.")


class ChatRequest(BaseModel):
    """Body request `/api/chat`."""

    message: str = Field(min_length=1, max_length=4000, description="Pesan user, atau jawaban untuk event `ask`.")
    thread_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


EVENT_NAMES = {StepEvent: "step", AskEvent: "ask", AnswerEvent: "answer"}


@router.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    """Chat dengan Server-Sent Events.

    Event: `start` {thread_id} -> `step` {tool, label, args} ... -> `answer` {text} | `ask` {question, options}
    -> (`error` {message}) -> `end` {}.
    Kirim `thread_id` yang sama untuk melanjutkan percakapan. Setelah event `ask`, kirim jawaban user
    (salah satu `options` atau teks bebas) sebagai `message` dengan `thread_id` yang sama.
    """
    thread_id = req.thread_id or uuid4().hex

    async def events() -> AsyncIterator[str]:
        yield _sse("start", {"thread_id": thread_id})
        try:
            async for event in run_agent(get_graph(), thread_id, req.message):
                yield _sse(EVENT_NAMES[type(event)], asdict(event))
        except Exception:
            logger.exception("Chat gagal (thread_id=%s)", thread_id)
            yield _sse("error", {"message": ERROR_MESSAGE})
        yield _sse("end", {})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Thread-Id": thread_id},
    )
