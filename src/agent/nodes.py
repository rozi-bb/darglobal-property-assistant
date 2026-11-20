"""Node-node graph. Tiap node = 1 fungsi yang mengerjakan 1 hal."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig

from agent.llm import get_llm
from agent.prompts import SYSTEM_PROMPT
from agent.state import State


async def call_model(state: State, config: RunnableConfig) -> dict[str, Any]:
    """[LLM] Kirim system prompt + riwayat pesan ke LLM, kembalikan jawabannya.

    `config` diteruskan supaya token bisa di-stream dan tercatat di trace.
    """
    llm = get_llm()
    response = await llm.ainvoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]], config)
    return {"messages": [response]}
