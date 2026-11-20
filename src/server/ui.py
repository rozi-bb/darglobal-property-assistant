"""UI chat (Chainlit), di-mount ke FastAPI di `/chat`.

Memakai `stream_reply()` yang sama dengan endpoint `/api/chat`. API key tetap di server.
"""

from __future__ import annotations

import logging
from uuid import uuid4

import chainlit as cl

from server.chat import ERROR_MESSAGE, stream_reply
from server.runtime import get_graph

logger = logging.getLogger(__name__)


@cl.set_starters
async def set_starters(user: cl.User | None = None, language: str | None = None) -> list[cl.Starter]:
    """Contoh pertanyaan di layar awal."""
    return [
        cl.Starter(label="DarGlobal projects in Saudi Arabia", message="Which DarGlobal projects are in Saudi Arabia?"),
        cl.Starter(label="What is Trump Tower Jeddah?", message="What is Trump Tower Jeddah?"),
        cl.Starter(
            label="Apartments for rent in Riyadh",
            message="Show me 3-bedroom apartments for rent in Riyadh under 80,000 SAR per year.",
        ),
        cl.Starter(
            label="Villa prices in Jeddah",
            message="What's the typical price of a villa for sale in Jeddah?",
        ),
    ]


@cl.on_chat_start
async def on_chat_start() -> None:
    """Satu sesi chat di UI = satu thread memori."""
    cl.user_session.set("thread_id", uuid4().hex)


@cl.on_message
async def on_message(message: cl.Message) -> None:
    """Teruskan pesan ke agent dan stream jawabannya ke layar."""
    thread_id: str = cl.user_session.get("thread_id") or uuid4().hex
    reply = cl.Message(content="")
    try:
        async for text in stream_reply(get_graph(), thread_id, message.content):
            await reply.stream_token(text)
    except Exception:
        logger.exception("Chat UI gagal (thread_id=%s)", thread_id)
        reply.content = ERROR_MESSAGE
    await reply.send()
