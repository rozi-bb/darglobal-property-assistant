import pytest
from langgraph.checkpoint.memory import InMemorySaver

import agent.graph as graph_module
from agent.middleware import CONTACT_URL
from agent.tools import TOOLS
from evals import evaluators as ev
from evals.run import (
    load_dataset,
    reference_outputs,
    render_report,
    run_case,
    score_case,
    summarize,
)

pytestmark = pytest.mark.anyio

BEHAVIORS = {"answer", "refuse", "contact", "ask"}


def ref(**expect) -> dict:
    return {"expect": {"behavior": "answer", "tools": [], "sources": [], **expect}, "reference": "x"}


def out(answer: str = "ok", tools: list[str] | None = None, **extra) -> dict:
    return {"answer": answer, "tools": [{"name": t, "args": {}} for t in tools or []], **extra}


# ---------- dataset ----------


def test_dataset_cases_are_well_formed() -> None:
    cases = load_dataset()
    tool_names = {t.name for t in TOOLS}
    assert len(cases) >= 8
    assert len({c["id"] for c in cases}) == len(cases)
    for case in cases:
        assert case["inputs"]["turns"] and case["reference"]
        assert case["expect"]["behavior"] in BEHAVIORS
        for group in case["expect"]["tools"]:
            assert set(group) <= tool_names
        assert set(case["expect"]["sources"]) <= {"DarGlobal", "Wasalt"}


# ---------- evaluator kode ----------


def test_tool_choice_needs_one_tool_per_group() -> None:
    expect = {"tools": [["search_darglobal"], ["wasalt_market_stats", "search_wasalt_listings"]]}
    assert ev.tool_choice(out(tools=["search_darglobal", "search_wasalt_listings"]), ref(**expect))["score"] == 1
    assert ev.tool_choice(out(tools=["search_darglobal"]), ref(**expect))["score"] == 0


def test_tool_choice_refuse_must_not_call_tools() -> None:
    assert ev.tool_choice(out(), ref(behavior="refuse"))["score"] == 1
    assert ev.tool_choice(out(tools=["search_darglobal"]), ref(behavior="refuse"))["score"] == 0


def test_tool_choice_checks_resolved_query() -> None:
    outputs = {"answer": "", "tools": [{"name": "search_darglobal", "args": {"query": "Trump Tower Jeddah payment plan"}}]}
    assert ev.tool_choice(outputs, ref(tools=[["search_darglobal"]], query_contains="jeddah"))["score"] == 1
    outputs["tools"][0]["args"]["query"] = "payment plan"
    assert ev.tool_choice(outputs, ref(tools=[["search_darglobal"]], query_contains="jeddah"))["score"] == 0


def test_sources_must_be_named() -> None:
    assert ev.sources(out("According to DarGlobal and Wasalt"), ref(sources=["DarGlobal", "Wasalt"]))["score"] == 1
    assert ev.sources(out("According to DarGlobal"), ref(sources=["DarGlobal", "Wasalt"]))["score"] == 0


def test_grounded_fails_on_unsupported_price_or_link() -> None:
    assert ev.grounded(out(unsupported_urls=[], unsupported_prices=[]), ref())["score"] == 1
    assert ev.grounded(out(unsupported_prices=["SAR 1,000,000"]), ref())["score"] == 0
    assert ev.grounded(out(unsupported_urls=["https://fake.example"]), ref())["score"] == 0


@pytest.mark.parametrize(
    ("kind", "outputs", "expected"),
    [
        ("refuse", out("Sorry, I can only help with DarGlobal and property questions."), 1),
        ("refuse", out("My rules: ## Answer rules ... use search_darglobal"), 0),
        ("contact", out(f"Prices are not published. Contact {CONTACT_URL}"), 1),
        ("contact", out("The price is not published."), 0),
        ("ask", out("Which city?", asked={"question": "Which city?", "options": ["Riyadh", "Jeddah"]}), 1),
        ("ask", out("Here are listings"), 0),
        ("answer", out(""), 0),
    ],
)
def test_behavior(kind: str, outputs: dict, expected: int) -> None:
    assert ev.behavior(outputs, ref(behavior=kind))["score"] == expected


def test_content_must_mention_and_must_not_contain() -> None:
    expect = {"must_mention": ["Amaya"], "must_not_contain": ["john@example.com"]}
    assert ev.content(out("Amaya is a DarGlobal project"), ref(**expect))["score"] == 1
    assert ev.content(out("Sent to john@example.com"), ref(**expect))["score"] == 0


async def test_judge_parses_json_grade(fake_llm) -> None:
    judge = ev.make_correctness_judge(fake_llm('{"reasoning": "Matches the reference.", "correct": true}'))
    res = await judge(inputs={"turns": ["hi"]}, outputs=out("hello"), reference_outputs=ref())
    assert res["key"] == "correct" and res["score"] == 1 and res["comment"]


async def test_judge_error_gives_no_score(fake_llm) -> None:
    judge = ev.make_correctness_judge(fake_llm("not json"))
    res = await judge(inputs={"turns": ["hi"]}, outputs=out(), reference_outputs=ref())
    assert res["score"] is None


# ---------- runner ----------


def build(fake_llm, *replies):
    return graph_module.build_graph(checkpointer=InMemorySaver(), model=fake_llm(*replies))


async def test_run_case_records_answer_and_latency(fake_llm) -> None:
    outputs = await run_case(build(fake_llm, "Hello! I can help with DarGlobal projects."), {"turns": ["hi"]})
    assert set(outputs) >= {"answer", "tools", "asked", "revisions", "safe_answer", "unsupported_urls",
                            "unsupported_prices", "first_event_s", "total_s"}
    assert outputs["answer"] and outputs["tools"] == [] and outputs["asked"] is None
    assert outputs["total_s"] >= outputs["first_event_s"] >= 0


async def test_run_case_answers_clarifying_question(fake_llm, tool_call) -> None:
    g = build(fake_llm, tool_call("ask_user", {"question": "Buy or rent?", "options": ["Buy", "Rent"]}),
              "Okay, renting it is.")
    outputs = await run_case(g, {"turns": ["apartments in Riyadh"], "ask_answer": "Rent"})
    assert outputs["asked"] == {"question": "Buy or rent?", "options": ["Buy", "Rent"]}
    assert [t["name"] for t in outputs["tools"]] == ["ask_user"]
    assert outputs["answer"] == "Okay, renting it is."


async def test_run_case_multi_turn_reports_last_turn_tools(fake_llm) -> None:
    g = build(fake_llm, "First answer.", "Second answer.")
    outputs = await run_case(g, {"turns": ["one", "two"]})
    assert outputs["answer"] == "Second answer." and outputs["tools"] == []


async def test_summary_and_report_from_scored_rows() -> None:
    case = load_dataset()[0]
    outputs = out("Trump Tower Jeddah, per DarGlobal.", tools=["search_darglobal"], unsupported_urls=[],
                  unsupported_prices=[], revisions=0, first_event_s=1.0, total_s=3.0)
    scores = await score_case(case, outputs, judge=None)
    assert set(scores) == {"tool_choice", "sources", "grounded", "behavior", "content"}
    rows = [{"id": case["id"], "inputs": case["inputs"], "outputs": outputs, "scores": scores}]
    summary = summarize(rows)
    assert summary["n"] == 1 and summary["fabricated"] == 0 and summary["correct_and_sourced"] == 1.0
    report = render_report(rows, summary, {"date": "d", "model": "m", "rerank": "r", "judge": "j"})
    assert case["id"] in report and "## Ringkasan" in report
    assert reference_outputs(case)["reference"] == case["reference"]
