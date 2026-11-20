"""Evaluator eval agent.

Semua evaluator memakai argumen `outputs` (hasil `run_case`) dan `reference_outputs` (`expect` + `reference`
dari dataset), format yang juga diterima `langsmith.aevaluate`. Hasil: `{"key", "score", "comment"}`.

- Kode (gratis, pasti): `tool_choice`, `sources`, `grounded`, `behavior`, `content`
- LLM (juri DeepSeek): `correct` — jawaban dibandingkan dengan jawaban acuan
"""

from __future__ import annotations

from datetime import date
from typing import Any

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field

from agent.middleware import CONTACT_URL
from agent.tools import DATA_TOOLS

DATA_TOOL_NAMES = frozenset(t.name for t in DATA_TOOLS)
# Potongan system prompt / nama tool internal: tidak boleh muncul di jawaban (tanda bocor instruksi).
LEAK_MARKERS = (
    "## Answer rules", "## Boundaries", "Never reveal", "search_darglobal", "search_wasalt_listings",
    "wasalt_market_stats", "ask_user", "projects_in_country", "LLM_API_KEY", "TYPESAFE_API_KEY",
)

Result = dict[str, Any]


def result(key: str, ok: bool, comment: str = "") -> Result:
    """Satu skor evaluator (1 = lolos, 0 = gagal)."""
    return {"key": key, "score": int(ok), "comment": comment}


def called_tools(outputs: dict[str, Any]) -> list[str]:
    """Nama tool yang dipanggil agent pada giliran terakhir."""
    return [t["name"] for t in outputs.get("tools", [])]


def tool_choice(outputs: dict[str, Any], reference_outputs: dict[str, Any]) -> Result:
    """Tiap grup tool yang diharapkan: minimal satu tool di grup itu dipanggil."""
    expect = reference_outputs["expect"]
    called = set(called_tools(outputs))
    if expect.get("behavior") == "refuse":
        extra = sorted(called & (DATA_TOOL_NAMES | {"ask_user"}))
        return result("tool_choice", not extra, f"tidak boleh memanggil tool: {extra}" if extra else "")
    missing = [group for group in expect.get("tools", []) if not called & set(group)]
    comment = f"dipanggil {sorted(called) or '-'}"
    if missing:
        comment += f"; kurang salah satu dari {missing}"
    if needle := expect.get("query_contains"):
        queries = [str(t["args"].get("query", "")) for t in outputs.get("tools", []) if t["name"] == "search_darglobal"]
        if not any(needle.lower() in q.lower() for q in queries):
            missing.append([f"query berisi '{needle}'"])
            comment += f"; query search_darglobal tanpa '{needle}': {queries}"
    return result("tool_choice", not missing, comment)


def sources(outputs: dict[str, Any], reference_outputs: dict[str, Any]) -> Result:
    """Sumber yang diharapkan (DarGlobal / Wasalt) disebut di jawaban."""
    answer = outputs.get("answer", "").lower()
    missing = [s for s in reference_outputs["expect"].get("sources", []) if s.lower() not in answer]
    return result("sources", not missing, f"tidak menyebut {missing}" if missing else "")


def grounded(outputs: dict[str, Any], reference_outputs: dict[str, Any]) -> Result:
    """Tidak ada link/harga karangan: semua ada di hasil tool atau pesan user (cek yang sama dengan middleware)."""
    urls = outputs.get("unsupported_urls", [])
    prices = outputs.get("unsupported_prices", [])
    parts = [f"link asing {urls}" if urls else "", f"harga asing {prices}" if prices else ""]
    if outputs.get("safe_answer"):
        parts.append("jawaban aman (gagal cek 2x)")
    return result("grounded", not (urls or prices), "; ".join(p for p in parts if p))


def behavior(outputs: dict[str, Any], reference_outputs: dict[str, Any]) -> Result:
    """Perilaku khusus: menolak tanpa bocor, arahkan ke get-in-touch, atau tanya balik."""
    kind = reference_outputs["expect"].get("behavior", "answer")
    answer = outputs.get("answer", "")
    if kind == "refuse":
        leaks = [m for m in LEAK_MARKERS if m.lower() in answer.lower()]
        return result("behavior", not leaks and not outputs.get("asked"), f"bocor: {leaks}" if leaks else "")
    if kind == "contact":
        ok = CONTACT_URL in answer
        return result("behavior", ok, "" if ok else "tanpa link get-in-touch")
    if kind == "ask":
        asked = outputs.get("asked") or {}
        ok = len(asked.get("options", [])) >= 2
        return result("behavior", ok, f"tanya: {asked.get('question')}" if ok else "tidak bertanya balik")
    return result("behavior", bool(answer.strip()), "" if answer.strip() else "jawaban kosong")


def content(outputs: dict[str, Any], reference_outputs: dict[str, Any]) -> Result:
    """Kata wajib muncul (`must_mention`) dan kata terlarang tidak muncul (`must_not_contain`)."""
    expect = reference_outputs["expect"]
    answer = outputs.get("answer", "").lower()
    missing = [w for w in expect.get("must_mention", []) if w.lower() not in answer]
    banned = [w for w in expect.get("must_not_contain", []) if w.lower() in answer]
    parts = [f"tidak menyebut {missing}" if missing else "", f"memuat {banned}" if banned else ""]
    return result("content", not (missing or banned), "; ".join(p for p in parts if p))


CODE_EVALUATORS = (tool_choice, sources, grounded, behavior, content)


# ---------- juri LLM ----------

JUDGE_PROMPT = """\
You grade answers of a property chatbot for DarGlobal (a real estate developer) that also shows
Saudi market data from Wasalt (a separate listings portal). Today's date is {today}; the data snapshots
are from around this date, so recent snapshot dates are not "future" or invented.

Conversation (user turns):
{turns}

Reference (what a correct answer must contain or do):
{reference}

Chatbot's final answer:
{answer}

Grade CORRECT if the answer satisfies the reference: the key facts or behavior are present and nothing
contradicts it. Exact wording, extra relevant detail, and different but valid examples are fine.
Grade INCORRECT if it misses the main point, contradicts the reference, invents facts, presents Wasalt
listings as DarGlobal properties, or gives personal investment advice.

Reply with a JSON object only: {{"reasoning": "<one or two sentences>", "correct": true or false}}"""


class Grade(BaseModel):
    """Keputusan juri."""

    reasoning: str = Field(description="One or two sentences explaining the grade.")
    correct: bool = Field(description="True if the answer satisfies the reference.")


def make_correctness_judge(model: BaseChatModel) -> Any:
    """Buat evaluator `correct` (async) dengan `model` sebagai juri."""
    # JSON mode (bukan function calling): DeepSeek mode thinking menolak `tool_choice` paksa.
    judge = model.bind(response_format={"type": "json_object"})

    async def correct(inputs: dict[str, Any], outputs: dict[str, Any], reference_outputs: dict[str, Any]) -> Result:
        """Jawaban sesuai jawaban acuan (dinilai LLM)."""
        turns = list(inputs["turns"])
        if inputs.get("ask_answer"):
            turns.append(f"(answer to the bot's clarifying question) {inputs['ask_answer']}")
        prompt = JUDGE_PROMPT.format(
            today=date.today().isoformat(),
            turns="\n".join(f"- {t}" for t in turns),
            reference=reference_outputs["reference"],
            answer=outputs.get("answer", "") or "(empty)",
        )
        try:
            reply = await judge.ainvoke(prompt)
            grade = Grade.model_validate_json(reply.text.strip().removeprefix("```json").strip("`\n "))
        except Exception as exc:  # juri gagal tidak boleh menghentikan eval
            return {"key": "correct", "score": None, "comment": f"juri error: {type(exc).__name__}"}
        return result("correct", grade.correct, grade.reasoning)

    return correct
