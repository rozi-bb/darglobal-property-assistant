"""Jalankan eval agent pada dataset PRD #15 (+ kasus tambahan).

    uv run python -m evals.run                 # lokal: LLM asli + Postgres lokal, laporan ke docs/eval-results.md
    uv run python -m evals.run --only q1_trump_tower_jeddah,q5_da_vinci_price
    uv run python -m evals.run --upload        # dataset + hasil ke LangSmith (butuh LANGSMITH_API_KEY)

Butuh `LLM_API_KEY` dan `CHECKPOINT_DATABASE_URL` (tabel data sudah dimuat + di-embed).
Agent dijalankan lewat `run_agent()` yang sama dengan UI/API, dengan checkpointer di memori.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from agent.middleware import (
    REFUSAL_ANSWER,
    SAFE_ANSWER,
    check_answer,
    current_run,
    is_feedback,
)
from evals.evaluators import CODE_EVALUATORS, Result, make_correctness_judge
from server.chat import AnswerEvent, AskEvent, run_agent

logger = logging.getLogger("evals")

DATASET = Path(__file__).with_name("dataset.jsonl")
DEFAULT_REPORT = Path("docs/eval-results.md")
DATASET_PREFIX = "darglobal-eval"
TARGET_CORRECT_AND_SOURCED = 0.8
EVAL_KEYS = ("tool_choice", "sources", "grounded", "behavior", "content", "correct")


def load_dataset(path: Path = DATASET) -> list[dict[str, Any]]:
    """Baca kasus eval (satu JSON per baris)."""
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def reference_outputs(case: dict[str, Any]) -> dict[str, Any]:
    """Bagian kasus yang dipakai evaluator (ekspektasi + jawaban acuan)."""
    return {"expect": case["expect"], "reference": case["reference"]}


async def run_case(graph: CompiledStateGraph, inputs: dict[str, Any]) -> dict[str, Any]:
    """Jalankan satu percakapan; kembalikan jawaban final, tool, tanya balik, latensi, dan hasil cek."""
    thread_id = f"eval-{uuid4().hex[:12]}"
    turns: list[str] = list(inputs["turns"])
    answer, asked = "", None
    first_event_s = total_s = 0.0
    for i, turn in enumerate(turns):
        last = i == len(turns) - 1
        message: str | None = turn
        start = time.perf_counter()
        first: float | None = None
        resumed = False
        while message is not None:
            pending = False
            async for event in run_agent(graph, thread_id, message):
                if first is None:
                    first = time.perf_counter() - start
                if isinstance(event, AskEvent):
                    pending = True
                    if last and asked is None:
                        asked = {"question": event.question, "options": event.options}
                    answer = event.question
                elif isinstance(event, AnswerEvent):
                    answer = event.text
            # Tanya balik dijawab sekali dengan `ask_answer` (seperti user menekan tombol); tanya kedua = akhir.
            if pending and inputs.get("ask_answer") and not resumed:
                message, resumed = str(inputs["ask_answer"]), True
            else:
                message = None
        if last:
            first_event_s, total_s = first or 0.0, time.perf_counter() - start

    state = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    messages = [m for m in state.values.get("messages", []) if not is_feedback(m)]
    tools = [
        {"name": call["name"], "args": call["args"]}
        for m in current_run(messages) if isinstance(m, AIMessage) for call in m.tool_calls
    ]
    check = check_answer(answer, messages)
    return {
        "answer": answer,
        "tools": tools,
        "asked": asked,
        "revisions": int(state.values.get("answer_revisions", 0) or 0),
        "safe_answer": answer.strip() == SAFE_ANSWER,
        "leak_blocked": answer.strip() == REFUSAL_ANSWER,  # pagar kebocoran (kode) mengganti jawaban model
        "unsupported_urls": check.unknown_urls,
        "unsupported_prices": check.unknown_prices,
        "first_event_s": round(first_event_s, 2),
        "total_s": round(total_s, 2),
    }


Evaluator = Callable[..., Any]


async def score_case(
    case: dict[str, Any], outputs: dict[str, Any], judge: Evaluator | None
) -> dict[str, Result]:
    """Jalankan semua evaluator untuk satu kasus."""
    ref = reference_outputs(case)
    scores = {r["key"]: r for r in (ev(outputs=outputs, reference_outputs=ref) for ev in CODE_EVALUATORS)}
    if judge is not None:
        scores["correct"] = await judge(inputs=case["inputs"], outputs=outputs, reference_outputs=ref)
    return scores


async def evaluate_local(
    cases: Sequence[dict[str, Any]], graph: CompiledStateGraph, judge: Evaluator | None, concurrency: int = 4
) -> list[dict[str, Any]]:
    """Eval tanpa LangSmith: jalankan kasus paralel (dibatasi), lalu nilai."""
    sem = asyncio.Semaphore(concurrency)

    async def one(case: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            try:
                outputs = await run_case(graph, case["inputs"])
            except Exception as exc:
                logger.exception("Kasus %s gagal", case["id"])
                outputs = {"answer": "", "tools": [], "error": f"{type(exc).__name__}: {exc}"}
            scores = await score_case(case, outputs, judge)
            logger.info("%-28s %s", case["id"], " ".join(f"{k}={v['score']}" for k, v in scores.items()))
            return {"id": case["id"], "inputs": case["inputs"], "outputs": outputs, "scores": scores}

    return list(await asyncio.gather(*(one(c) for c in cases)))


async def evaluate_langsmith(
    cases: Sequence[dict[str, Any]], graph: CompiledStateGraph, judge: Evaluator | None, concurrency: int = 4
) -> list[dict[str, Any]]:
    """Unggah dataset (nama berisi hash isi, jadi idempoten) lalu jalankan `aevaluate` di LangSmith."""
    from langsmith import Client, aevaluate

    client = Client()
    digest = hashlib.sha1(json.dumps(cases, sort_keys=True).encode()).hexdigest()[:8]
    name = f"{DATASET_PREFIX}-{digest}"
    if not client.has_dataset(dataset_name=name):
        dataset = client.create_dataset(name, description="DarGlobal chatbot eval (PRD #15 + kasus tambahan)")
        client.create_examples(dataset_id=dataset.id, examples=[
            {"inputs": c["inputs"], "outputs": reference_outputs(c), "metadata": {"case_id": c["id"]}} for c in cases
        ])
        logger.info("Dataset LangSmith dibuat: %s (%d contoh)", name, len(cases))

    async def target(inputs: dict[str, Any]) -> dict[str, Any]:
        return await run_case(graph, inputs)

    evaluators: list[Evaluator] = [*CODE_EVALUATORS, *([judge] if judge else [])]
    results = await aevaluate(target, data=name, evaluators=evaluators, experiment_prefix="darglobal-agent",
                              max_concurrency=concurrency)
    rows: list[dict[str, Any]] = []
    async for r in results:
        example, run = r["example"], r["run"]
        scores = {e.key: {"key": e.key, "score": e.score, "comment": e.comment or ""}
                  for e in r["evaluation_results"]["results"]}
        rows.append({"id": (example.metadata or {}).get("case_id", str(example.id)), "inputs": example.inputs,
                     "outputs": run.outputs or {}, "scores": scores})
    logger.info("Experiment LangSmith: %s", results.experiment_name)
    return rows


# ---------- ringkasan + laporan ----------


def passed(row: dict[str, Any], key: str) -> bool:
    """Skor evaluator `key` = 1."""
    return row["scores"].get(key, {}).get("score") == 1


def summarize(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Angka utama dibanding target PRD #12."""
    n = len(rows)
    rate = {k: sum(passed(r, k) for r in rows) / n for k in EVAL_KEYS if any(k in r["scores"] for r in rows)}
    firsts = [r["outputs"].get("first_event_s", 0.0) for r in rows if "error" not in r["outputs"]]
    totals = [r["outputs"].get("total_s", 0.0) for r in rows if "error" not in r["outputs"]]
    judged = any("correct" in r["scores"] for r in rows)
    ok_sourced = sum((passed(r, "correct") or not judged) and passed(r, "sources") for r in rows) / n
    return {
        "n": n,
        "rate": rate,
        "correct_and_sourced": ok_sourced,
        "fabricated": sum(not passed(r, "grounded") for r in rows),
        "all_pass": sum(all(passed(r, k) for k in r["scores"]) for r in rows),
        "errors": sum("error" in r["outputs"] for r in rows),
        "leak_blocked": sum(bool(r["outputs"].get("leak_blocked")) for r in rows),
        "first_event_p50_s": round(statistics.median(firsts), 2) if firsts else None,
        "total_p50_s": round(statistics.median(totals), 2) if totals else None,
        "total_max_s": round(max(totals), 2) if totals else None,
        "judged": judged,
    }


def mark(row: dict[str, Any], key: str) -> str:
    """Simbol tabel: ✅ lolos, ❌ gagal, – tidak dinilai."""
    score = row["scores"].get(key, {}).get("score")
    return "–" if score is None else ("✅" if score == 1 else "❌")


def render_report(rows: Sequence[dict[str, Any]], summary: dict[str, Any], meta: dict[str, str]) -> str:
    """Laporan markdown (ringkasan, tabel per kasus, jawaban lengkap)."""
    target_ok = summary["correct_and_sourced"] >= TARGET_CORRECT_AND_SOURCED
    lines = [
        "# Hasil eval agent",
        "",
        f"Dibuat `uv run python -m evals.run` · {meta['date']} · model `{meta['model']}` · "
        f"rerank Jev: {meta['rerank']} · juri: {meta['judge']}",
        "",
        "## Ringkasan vs target PRD #12",
        "",
        "| Metrik | Hasil | Target |",
        "|---|---|---|",
        f"| Benar & ber-sumber | {summary['correct_and_sourced']:.0%} ({'✅' if target_ok else '❌'}) | ≥ 80% |",
        f"| Jawaban dengan link/harga karangan | {summary['fabricated']} ({'✅' if not summary['fabricated'] else '❌'})"
        " | 0 |",
        f"| Lolos semua evaluator | {summary['all_pass']}/{summary['n']} | – |",
        f"| Event pertama (p50, lokal) | {summary['first_event_p50_s']} dtk | < 5 dtk (diukur di production) |",
        f"| Jawaban final (p50 / maks) | {summary['total_p50_s']} / {summary['total_max_s']} dtk | – |",
        f"| Error run | {summary['errors']} | 0 |",
        f"| Jawaban diblokir pagar kebocoran (kode) | {summary['leak_blocked']} | – |",
        "",
        "Per evaluator: " + " · ".join(f"`{k}` {v:.0%}" for k, v in summary["rate"].items()),
        "",
        "## Per kasus",
        "",
        "| Kasus | Tool | Sumber | Grounded | Perilaku | Konten | Benar (juri) | Revisi | Final (dtk) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        o = r["outputs"]
        lines.append(
            f"| `{r['id']}` | {mark(r, 'tool_choice')} | {mark(r, 'sources')} | {mark(r, 'grounded')} | "
            f"{mark(r, 'behavior')} | {mark(r, 'content')} | {mark(r, 'correct')} | {o.get('revisions', '–')} | "
            f"{o.get('total_s', '–')} |"
        )
    lines += ["", "## Detail", ""]
    for r in rows:
        o = r["outputs"]
        notes = [f"`{k}`: {v['comment']}" for k, v in r["scores"].items() if v.get("comment") and v.get("score") != 1]
        lines += [
            f"### `{r['id']}`",
            "",
            *[f"> **User:** {t}" for t in r["inputs"]["turns"]],
            *([f"> **Tanya balik:** {o['asked']['question']} {o['asked']['options']} → "
               f"**{r['inputs'].get('ask_answer')}**"] if o.get("asked") else []),
            "",
            f"Tool: {', '.join(t['name'] for t in o.get('tools', [])) or '-'}",
            "",
            *(["Pagar kebocoran (kode) mengganti jawaban model dengan penolakan.", ""] if o.get("leak_blocked") else []),
            *([f"Catatan gagal: {'; '.join(notes)}", ""] if notes else []),
            *([f"Error: {o['error']}", ""] if o.get("error") else []),
            "<details><summary>Jawaban</summary>",
            "",
            o.get("answer", ""),
            "",
            "</details>",
            "",
        ]
    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> int:
    """Muat dataset, jalankan eval, tulis laporan. Exit 1 kalau target PRD tidak tercapai."""
    from agent import db
    from agent.graph import build_graph
    from agent.llm import get_llm

    cases = load_dataset()
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    if not cases:
        raise SystemExit("Tidak ada kasus yang cocok.")
    graph = build_graph(checkpointer=InMemorySaver())
    judge = None if args.no_judge else make_correctness_judge(get_llm())
    run = evaluate_langsmith if args.upload else evaluate_local
    try:
        rows = await run(cases, graph, judge, args.concurrency)
    finally:
        await db.close_pool()

    order = {c["id"]: i for i, c in enumerate(cases)}
    rows = sorted(rows, key=lambda r: order.get(r["id"], len(order)))
    summary = summarize(rows)
    meta = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "model": os.environ.get("LLM_MODEL") or "deepseek-flash",
        "rerank": "aktif" if os.environ.get("TYPESAFE_API_KEY", "").strip() else "nonaktif (tanpa key, urutan RRF)",
        "judge": "nonaktif" if args.no_judge else "LLM yang sama (DeepSeek)",
    }
    report = render_report(rows, summary, meta)
    if args.out:
        Path(args.out).write_text(report)
        logger.info("Laporan: %s", args.out)
    sys.stdout.write(json.dumps({k: v for k, v in summary.items() if k != "judged"}, indent=2) + "\n")
    ok = summary["correct_and_sourced"] >= TARGET_CORRECT_AND_SOURCED and summary["fabricated"] == 0
    return 0 if ok else 1


def main() -> None:
    """Entry point CLI."""
    parser = argparse.ArgumentParser(description="Eval agent DarGlobal")
    parser.add_argument("--only", help="id kasus, dipisah koma")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--upload", action="store_true", help="unggah dataset + hasil ke LangSmith")
    parser.add_argument("--no-judge", action="store_true", help="tanpa juri LLM (hanya evaluator kode)")
    parser.add_argument("--out", default=str(DEFAULT_REPORT), help="file laporan markdown ('' = tidak ditulis)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "openai", "langsmith"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    load_dotenv(override=False)
    if not os.environ.get("LANGSMITH_API_KEY"):
        if args.upload:
            raise SystemExit("--upload butuh LANGSMITH_API_KEY di .env.")
        os.environ["LANGSMITH_TRACING"] = "false"
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
