"""Graph yang di-export untuk `langgraph dev` (langgraph.json: `./src/agent/graph.py:graph`)."""

import pytest
from langgraph.pregel import Pregel

from agent import graph as graph_module
from agent import llm


def test_exported_graph_builds_without_calling_api(monkeypatch: pytest.MonkeyPatch) -> None:
    # Membuat client ChatOpenAI tidak memanggil API, jadi key dummy cukup (CI tidak punya .env).
    monkeypatch.setenv("LLM_API_KEY", "dummy-key-for-test")
    llm.get_llm.cache_clear()
    graph_module._dev_graph.cache_clear()
    try:
        assert isinstance(graph_module.graph, Pregel)
    finally:
        llm.get_llm.cache_clear()
        graph_module._dev_graph.cache_clear()
