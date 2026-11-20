"""Tempat menyimpan graph yang sudah di-compile (dengan checkpointer).

Diisi sekali saat FastAPI start (lifespan), lalu dipakai endpoint `/api/chat`
dan UI Chainlit yang berjalan di proses yang sama.
"""

from __future__ import annotations

from langgraph.graph.state import CompiledStateGraph

_graph: CompiledStateGraph | None = None


def set_graph(graph: CompiledStateGraph | None) -> None:
    """Simpan graph aktif (None saat shutdown)."""
    global _graph
    _graph = graph


def get_graph() -> CompiledStateGraph:
    """Ambil graph aktif. Error kalau server belum selesai start."""
    if _graph is None:
        raise RuntimeError("Graph belum siap: server belum selesai start.")
    return _graph
