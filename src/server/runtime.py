"""Tempat menyimpan checkpointer + graph agent untuk FastAPI dan UI Chainlit (proses yang sama).

Checkpointer diisi saat FastAPI start (lifespan). Graph dibuat saat pertama dipakai, jadi server tetap
bisa start walau `LLM_API_KEY` belum diisi (chat lalu membalas pesan error yang ramah).
"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from agent.graph import build_graph

_checkpointer: BaseCheckpointSaver | None = None
_graph: CompiledStateGraph | None = None


def set_checkpointer(checkpointer: BaseCheckpointSaver | None) -> None:
    """Simpan checkpointer aktif (None saat shutdown); graph lama dibuang."""
    global _checkpointer, _graph
    _checkpointer, _graph = checkpointer, None


def get_graph() -> CompiledStateGraph:
    """Graph agent dengan checkpointer aktif. Error kalau server belum selesai start."""
    global _graph
    if _checkpointer is None:
        raise RuntimeError("Graph belum siap: server belum selesai start.")
    if _graph is None:
        _graph = build_graph(checkpointer=_checkpointer)
    return _graph
