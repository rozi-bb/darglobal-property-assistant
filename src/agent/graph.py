"""Konstruksi graph agent.

Baseline: START -> call_model -> END.

- `graph` (tanpa checkpointer) dipakai `langgraph dev`; persistence diurus server dev.
- `build_graph(checkpointer)` dipakai backend FastAPI dengan checkpointer Postgres.
"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agent.nodes import call_model
from agent.state import State


def build_graph(checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:
    """Rakit dan compile graph."""
    builder = StateGraph(State)
    builder.add_node("call_model", call_model)
    builder.add_edge(START, "call_model")
    builder.add_edge("call_model", END)
    return builder.compile(checkpointer=checkpointer, name="DarGlobal Assistant")


graph = build_graph()
