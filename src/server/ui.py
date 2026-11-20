"""UI chat (Chainlit), di-mount ke FastAPI di `/chat`.

Memakai `run_agent()` yang sama dengan endpoint `/api/chat`. API key tetap di server.
- Langkah agent (panggil tool) tampil sebagai `cl.Step`.
- Tanya balik (`ask_user`) tampil sebagai tombol; pilihan user melanjutkan agent di thread yang sama.
- Jawaban final dikirim setelah lolos cek jawaban.
"""

from __future__ import annotations

import logging
from uuid import uuid4

import chainlit as cl

from server.chat import ERROR_MESSAGE, AnswerEvent, AskEvent, StepEvent, run_agent
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


ASK_TIMEOUT_S = 600


async def show_step(event: StepEvent) -> None:
    """Tampilkan satu langkah agent (nama tool + argumen)."""
    async with cl.Step(name=event.label, type="tool", show_input="json") as step:
        step.input = event.args
        step.output = "done"


async def ask_with_buttons(event: AskEvent) -> str | None:
    """Tampilkan pertanyaan + tombol. None kalau user tidak memilih (boleh ketik jawaban bebas)."""
    actions = [cl.Action(name="ask_user_option", payload={"value": opt}, label=opt) for opt in event.options]
    res = await cl.AskActionMessage(content=event.question, actions=actions, timeout=ASK_TIMEOUT_S).send()
    if not res:
        return None
    return str(res.get("payload", {}).get("value") or res.get("label") or "")


@cl.on_message
async def on_message(message: cl.Message) -> None:
    """Teruskan pesan ke agent; tampilkan langkah, tanya balik, lalu jawaban final."""
    thread_id: str = cl.user_session.get("thread_id") or uuid4().hex
    text: str | None = message.content
    try:
        while text:
            next_text: str | None = None
            async for event in run_agent(get_graph(), thread_id, text):
                if isinstance(event, StepEvent):
                    await show_step(event)
                elif isinstance(event, AskEvent):
                    next_text = await ask_with_buttons(event)
                elif isinstance(event, AnswerEvent):
                    await cl.Message(content=event.text).send()
            text = next_text
    except Exception:
        logger.exception("Chat UI gagal (thread_id=%s)", thread_id)
        await cl.Message(content=ERROR_MESSAGE).send()
