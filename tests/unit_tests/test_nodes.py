import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent.graph import build_graph
from agent.nodes import call_model

pytestmark = pytest.mark.anyio


async def test_call_model_returns_ai_message(fake_llm) -> None:
    fake_llm("Hello from DarGlobal")
    result = await call_model({"messages": [HumanMessage("hi")]}, {})
    assert set(result) == {"messages"}
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], AIMessage)
    assert result["messages"][0].content


async def test_graph_remembers_thread(fake_llm) -> None:
    fake_llm("first answer", "second answer")
    graph = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t1"}}

    await graph.ainvoke({"messages": [HumanMessage("one")]}, config)
    state = await graph.ainvoke({"messages": [HumanMessage("two")]}, config)

    # 2 pesan user + 2 jawaban dalam thread yang sama
    assert [type(m) for m in state["messages"]] == [HumanMessage, AIMessage, HumanMessage, AIMessage]


async def test_threads_are_isolated(fake_llm) -> None:
    fake_llm("a", "b")
    graph = build_graph(checkpointer=InMemorySaver())

    await graph.ainvoke({"messages": [HumanMessage("x")]}, {"configurable": {"thread_id": "A"}})
    state_b = await graph.ainvoke({"messages": [HumanMessage("y")]}, {"configurable": {"thread_id": "B"}})

    assert len(state_b["messages"]) == 2
