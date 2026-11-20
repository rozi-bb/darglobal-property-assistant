"""Agent `create_agent` + middleware dengan fake model (tanpa API & tanpa DB)."""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent import graph as graph_module
from agent import wasalt
from agent.middleware import SAFE_ANSWER, is_feedback

pytestmark = pytest.mark.anyio

LISTING_URL = "https://wasalt.sa/en/property/rent/apartment-1"


@pytest.fixture
def fake_wasalt(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(sql: str, params: dict) -> list[dict]:
        return [{"id": 1, "title": "Apartment", "city": "Riyadh", "purpose": "rent", "property_type": "Apartment",
                 "price_sar": 60000.0, "rent_period": "yearly", "rent_yearly_sar": 60000.0, "bedrooms": 3,
                 "url": LISTING_URL, "snapshot_date": "2026-11-20", "total_matches": 1}]

    monkeypatch.setattr(wasalt, "fetch_all", fake_fetch)


def build(fake_llm, *replies) -> object:
    fake_llm(*replies)
    return graph_module.build_graph(checkpointer=InMemorySaver())


def cfg(thread: str = "t1") -> dict:
    return {"configurable": {"thread_id": thread}}


async def test_plain_answer_passes(fake_llm) -> None:
    g = build(fake_llm, "Hello! I can help with DarGlobal projects.")
    state = await g.ainvoke({"messages": [HumanMessage("hi")]}, cfg())
    assert [type(m) for m in state["messages"]] == [HumanMessage, AIMessage]


async def test_graph_remembers_thread_and_isolates_others(fake_llm) -> None:
    g = build(fake_llm, "a", "b", "c")
    await g.ainvoke({"messages": [HumanMessage("one")]}, cfg("A"))
    state_a = await g.ainvoke({"messages": [HumanMessage("two")]}, cfg("A"))
    state_b = await g.ainvoke({"messages": [HumanMessage("x")]}, cfg("B"))
    assert len(state_a["messages"]) == 4 and len(state_b["messages"]) == 2


async def test_tool_call_then_grounded_answer(fake_llm, fake_wasalt, tool_call) -> None:
    g = build(
        fake_llm,
        tool_call("search_wasalt_listings", {"city": "Riyadh", "purpose": "rent", "bedrooms": 3}),
        f"From Wasalt: a 3-bedroom apartment for 60,000 SAR per year: {LISTING_URL}",
    )
    state = await g.ainvoke({"messages": [HumanMessage("3BR rent Riyadh")]}, cfg())
    tool_msgs = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1 and json.loads(tool_msgs[0].content)["source"] == "Wasalt"
    assert state["messages"][-1].content.startswith("From Wasalt")


async def test_answer_check_forces_revision_and_hides_draft(fake_llm, fake_wasalt, tool_call) -> None:
    g = build(
        fake_llm,
        tool_call("search_wasalt_listings", {"city": "Riyadh", "purpose": "rent"}),
        "Great deal at 45,000 SAR: https://wasalt.sa/en/property/made-up",  # harga & link karangan
        f"On Wasalt, this apartment is 60,000 SAR per year: {LISTING_URL}",
    )
    state = await g.ainvoke({"messages": [HumanMessage("rent Riyadh")]}, cfg())
    texts = [str(m.content) for m in state["messages"]]
    assert state["messages"][-1].content.startswith("On Wasalt")
    assert not any("made-up" in t for t in texts)  # draf gagal dihapus
    assert not any(is_feedback(m) for m in state["messages"])  # catatan cek dibersihkan


async def test_answer_check_gives_safe_answer_after_two_revisions(fake_llm) -> None:
    bad = "It costs AED 9,999,999. See https://example.com/fake"
    g = build(fake_llm, bad, bad, bad)
    state = await g.ainvoke({"messages": [HumanMessage("price of Da Vinci Tower?")]}, cfg())
    assert state["messages"][-1].content == SAFE_ANSWER
    assert [type(m) for m in state["messages"]] == [HumanMessage, AIMessage]


async def test_ask_user_interrupts_and_resumes(fake_llm, fake_wasalt, tool_call) -> None:
    g = build(
        fake_llm,
        tool_call("ask_user", {"question": "Buy or rent?", "options": ["Buy", "Rent"]}),
        tool_call("search_wasalt_listings", {"city": "Riyadh", "purpose": "rent"}, call_id="call_2"),
        f"Here is one Wasalt listing: {LISTING_URL}",
    )
    first = await g.ainvoke({"messages": [HumanMessage("apartments in Riyadh")]}, cfg())
    assert first["__interrupt__"][0].value == {"type": "ask_user", "question": "Buy or rent?",
                                               "options": ["Buy", "Rent"]}
    state = await g.ainvoke(Command(resume="Rent"), cfg())
    answer = next(m for m in state["messages"] if isinstance(m, ToolMessage) and m.name == "ask_user")
    assert json.loads(answer.content)["user_answer"] == "Rent"
    assert "Wasalt" in state["messages"][-1].content


async def test_pii_is_masked_before_model(fake_llm) -> None:
    model = fake_llm("Thanks, noted.")
    g = graph_module.build_graph(checkpointer=InMemorySaver(), model=model)
    state = await g.ainvoke({"messages": [HumanMessage("mail me at jane.doe@example.com")]}, cfg())
    assert "jane.doe@example.com" not in state["messages"][0].content


def test_graph_module_import_needs_no_api_key() -> None:
    # `graph` dibuat saat diakses (langgraph dev), bukan saat import modul
    assert "graph" not in vars(graph_module)
