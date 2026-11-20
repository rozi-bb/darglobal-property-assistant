"""Konstruksi agent ReAct: `create_agent` (LangChain v1) + middleware.

- `graph` (tanpa checkpointer) dipakai `langgraph dev`; persistence diurus server dev.
  Dibuat saat pertama diakses (bukan saat import), jadi import modul ini tidak butuh `LLM_API_KEY`.
- `build_graph(checkpointer)` dipakai backend FastAPI dengan checkpointer Postgres.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ModelFallbackMiddleware,
    ModelRetryMiddleware,
    PIIMiddleware,
    SummarizationMiddleware,
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
)
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from agent.llm import get_fallback_llm, get_llm
from agent.middleware import (
    MAX_REVISIONS,
    AnswerCheckMiddleware,
    system_prompt_with_date,
)
from agent.tools import DATA_TOOLS, TOOLS

GRAPH_NAME = "DarGlobal Assistant"
MAX_TOOL_CALLS = 6
# 6 putaran tool + 1 jawaban + 2 revisi cek jawaban
MAX_MODEL_CALLS = MAX_TOOL_CALLS + 1 + MAX_REVISIONS
# Nomor telepon diawali +, 00, atau 0 (supaya harga seperti "2 500 000" tidak ikut tersamar).
PHONE_REGEX = r"(?<![\w.,])(?:\+|00|0)\d[\d\s\-()]{7,}\d(?!\w)"


def build_middleware(model: BaseChatModel, fallback: BaseChatModel | None = None) -> list[AgentMiddleware[Any, Any]]:
    """Urutan: before_model jalan dari atas ke bawah; wrap_model_call: yang pertama paling luar."""
    middleware: list[AgentMiddleware[Any, Any]] = [
        PIIMiddleware("email", strategy="redact"),
        PIIMiddleware("credit_card", strategy="mask"),
        PIIMiddleware("phone_number", detector=PHONE_REGEX, strategy="mask"),
        SummarizationMiddleware(model, trigger=("tokens", 12000), keep=("messages", 20)),
        system_prompt_with_date,
        ModelCallLimitMiddleware(run_limit=MAX_MODEL_CALLS, exit_behavior="end"),
        ToolCallLimitMiddleware(run_limit=MAX_TOOL_CALLS, exit_behavior="continue"),
    ]
    if fallback is not None:
        middleware.append(ModelFallbackMiddleware(fallback))  # di luar retry: primary di-retry dulu
    middleware += [
        ModelRetryMiddleware(max_retries=2, initial_delay=1.0),
        ToolRetryMiddleware(max_retries=2, tools=list(DATA_TOOLS), initial_delay=0.5),
        AnswerCheckMiddleware(),
    ]
    return middleware


def build_graph(
    checkpointer: BaseCheckpointSaver | None = None,
    model: BaseChatModel | None = None,
    fallback: BaseChatModel | None = None,
) -> CompiledStateGraph:
    """Rakit agent. `model`/`fallback` bisa diganti (test pakai fake model)."""
    model = model or get_llm()
    fallback = fallback if fallback is not None else get_fallback_llm()
    return create_agent(
        model,
        tools=TOOLS,
        middleware=build_middleware(model, fallback),
        checkpointer=checkpointer,
        name=GRAPH_NAME,
    )


@lru_cache(maxsize=1)
def _dev_graph() -> CompiledStateGraph:
    return build_graph()


def __getattr__(name: str) -> Any:
    """`graph` untuk `langgraph dev` (langgraph.json: `./src/agent/graph.py:graph`), dibuat saat diakses."""
    if name == "graph":
        return _dev_graph()
    raise AttributeError(name)
