"""Middleware custom: prompt dinamis + cek jawaban (link, harga, sumber).

Cek jawaban murni kode (tanpa LLM): draf final yang menyebut link/harga yang tidak ada di hasil tool,
atau tidak menyebut sumber, dikembalikan ke model dengan catatan perbaikan (maks 2 revisi).
Draf yang gagal dihapus dari riwayat, jadi tidak pernah tampil ke user.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import uuid4

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    dynamic_prompt,
    hook_config,
)
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    RemoveMessage,
    ToolMessage,
)
from langgraph.runtime import Runtime
from typing_extensions import NotRequired

from agent.prompts import SYSTEM_PROMPT

MAX_REVISIONS = 2
FEEDBACK_ID_PREFIX = "answer-check-"
CONTACT_URL = "https://darglobal.co.uk/get-in-touch"
ALWAYS_ALLOWED_URLS = {CONTACT_URL, "https://darglobal.co.uk", "https://wasalt.sa", "https://www.wasalt.sa"}
SAFE_ANSWER = (
    "I'm sorry, I can't confirm that from the DarGlobal and Wasalt data I have. "
    f"For accurate details, please contact DarGlobal at {CONTACT_URL}."
)
SOURCE_BY_TOOL = {
    "search_darglobal": "DarGlobal",
    "search_wasalt_listings": "Wasalt",
    "wasalt_market_stats": "Wasalt",
}

URL_RE = re.compile(r"https?://[^\s)\]>\"'`*|]+")
_CURRENCY = r"(?:SAR|SR|AED|USD|GBP|EUR|OMR|QAR|riyals?|dirhams?|\$|£|€)"
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_MULT = r"(?:\s?(million|mn|bn|billion|thousand|k|m)(?![a-z²2]))?"
PRICE_RE = re.compile(rf"{_CURRENCY}\s?({_NUM}){_MULT}|({_NUM}){_MULT}\s?{_CURRENCY}", re.IGNORECASE)
PLAIN_NUM_RE = re.compile(_NUM)
MULTIPLIERS = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9}


@dynamic_prompt
def system_prompt_with_date(request: ModelRequest) -> str:
    """System prompt + tanggal hari ini (agent tahu umur snapshot data)."""
    return f"{SYSTEM_PROMPT}\nToday's date: {date.today().isoformat()}."


# ---------- ekstraksi angka & link ----------


@dataclass(frozen=True)
class Price:
    """Harga yang disebut di teks + toleransi pembulatan sesuai jumlah digit yang ditulis."""

    text: str
    value: float
    tolerance: float


def normalize_url(url: str) -> str:
    """Samakan URL untuk dibandingkan (tanpa tanda baca/slash di akhir, huruf kecil)."""
    return url.rstrip(".,;:!?").rstrip("/").lower()


def extract_urls(text: str) -> set[str]:
    """Semua URL di teks (sudah dinormalisasi)."""
    return {normalize_url(u) for u in URL_RE.findall(text)}


def _to_price(num: str, mult: str | None, text: str) -> Price:
    factor = MULTIPLIERS.get((mult or "").lower(), 1.0)
    clean = num.replace(",", "")
    decimals = len(clean.split(".")[1]) if "." in clean else 0
    return Price(text=text.strip(), value=float(clean) * factor, tolerance=0.5 * 10 ** (-decimals) * factor)


def extract_prices(text: str) -> list[Price]:
    """Angka harga yang ditulis bersama mata uang: 'SAR 1,800,000', '1.8 million SAR', '40k AED'."""
    out = []
    for m in PRICE_RE.finditer(text):
        num, mult = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        out.append(_to_price(num, mult, m.group(0)))
    return out


def extract_numbers(text: str) -> set[float]:
    """Semua angka (termasuk yang berpengali) di teks hasil tool / pesan user."""
    values = {float(n.replace(",", "")) for n in PLAIN_NUM_RE.findall(text)}
    values.update(p.value for p in extract_prices(text))
    return values


def price_supported(price: Price, known: set[float]) -> bool:
    """Harga dianggap benar kalau sama dengan angka di data (toleransi pembulatan atau 1%)."""
    return any(abs(price.value - k) <= max(price.tolerance, 0.01 * abs(k)) for k in known)


# ---------- cek jawaban ----------


@dataclass
class CheckResult:
    """Hasil cek satu draf jawaban."""

    unknown_urls: list[str] = field(default_factory=list)
    unknown_prices: list[str] = field(default_factory=list)
    missing_sources: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Lolos kalau tidak ada link/harga asing dan semua sumber disebut."""
        return not (self.unknown_urls or self.unknown_prices or self.missing_sources)

    def feedback(self) -> str:
        """Catatan perbaikan untuk model (tidak pernah ditampilkan ke user)."""
        lines = ["[Automatic answer check] Your draft answer was NOT shown to the user. Rewrite it:"]
        if self.unknown_urls:
            lines.append(f"- These links are not in any tool result; remove them or use exact links from tool results: "
                         f"{', '.join(self.unknown_urls)}")
        if self.unknown_prices:
            lines.append(f"- These prices are not in any tool result; only quote prices exactly as the tools returned "
                         f"them: {', '.join(self.unknown_prices)}")
        if self.missing_sources:
            lines.append(f"- Name the source of the facts you used: {', '.join(self.missing_sources)}.")
        lines.append("Do not mention this check to the user.")
        return "\n".join(lines)


def is_feedback(msg: AnyMessage) -> bool:
    """Pesan catatan dari cek jawaban (bukan pertanyaan user)?"""
    return isinstance(msg, HumanMessage) and str(msg.id or "").startswith(FEEDBACK_ID_PREFIX)


def current_run(messages: list[AnyMessage]) -> list[AnyMessage]:
    """Pesan sejak pertanyaan user terakhir (feedback cek jawaban tidak dihitung sebagai pertanyaan)."""
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage) and not is_feedback(messages[i]):
            return messages[i:]
    return messages


def used_sources(run: list[AnyMessage]) -> set[str]:
    """Sumber yang hasil tool-nya berisi data (status ok) pada run ini."""
    sources = set()
    for msg in run:
        if isinstance(msg, ToolMessage) and msg.name in SOURCE_BY_TOOL:
            try:
                status = json.loads(str(msg.content)).get("status")
            except (ValueError, AttributeError):
                status = None
            if status == "ok":
                sources.add(SOURCE_BY_TOOL[msg.name])
    return sources


def check_answer(answer: str, messages: list[AnyMessage]) -> CheckResult:
    """Cek draf jawaban terhadap hasil tool di thread ini (riwayat + run sekarang)."""
    evidence = "\n".join(str(m.content) for m in messages if isinstance(m, ToolMessage))
    user_text = "\n".join(str(m.content) for m in messages if isinstance(m, HumanMessage) and not is_feedback(m))
    known_urls = extract_urls(evidence) | {normalize_url(u) for u in ALWAYS_ALLOWED_URLS}
    known_numbers = extract_numbers(evidence) | extract_numbers(user_text)

    result = CheckResult()
    result.unknown_urls = sorted({u for u in URL_RE.findall(answer) if normalize_url(u) not in known_urls})
    result.unknown_prices = [p.text for p in extract_prices(answer) if not price_supported(p, known_numbers)]
    lowered = answer.lower()
    result.missing_sources = sorted(s for s in used_sources(current_run(messages)) if s.lower() not in lowered)
    return result


def answer_text(msg: AIMessage) -> str:
    """Teks dari pesan AI (content bisa string atau list blok)."""
    content = msg.content
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")


class AnswerCheckState(AgentState):
    """State agent + jumlah revisi cek jawaban pada run ini."""

    answer_revisions: NotRequired[int]


class AnswerCheckMiddleware(AgentMiddleware[AnswerCheckState]):
    """Cek draf final (pesan AI tanpa tool_calls); gagal → model mengulang dengan catatan, maks 2 revisi."""

    state_schema = AnswerCheckState

    def __init__(self, max_revisions: int = MAX_REVISIONS) -> None:
        """`max_revisions`: berapa kali model boleh memperbaiki draf sebelum jawaban aman."""
        super().__init__()
        self.max_revisions = max_revisions

    def before_agent(self, state: AnswerCheckState, runtime: Runtime) -> dict[str, Any] | None:
        """Reset hitungan revisi di awal tiap run."""
        return {"answer_revisions": 0}

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: AnswerCheckState, runtime: Runtime) -> dict[str, Any] | None:
        """Cek draf final; gagal → hapus draf, tambah catatan, ulangi model (maks `max_revisions`)."""
        messages = state["messages"]
        draft = messages[-1] if messages else None
        if not isinstance(draft, AIMessage) or draft.tool_calls:
            return None
        cleanup = [RemoveMessage(id=m.id) for m in current_run(messages) if is_feedback(m) and m.id]
        result = check_answer(answer_text(draft), messages[:-1])
        if result.ok:
            return {"messages": cleanup} if cleanup else None

        revisions = state.get("answer_revisions", 0)
        remove_draft = [RemoveMessage(id=draft.id)] if draft.id else []
        if revisions >= self.max_revisions:
            return {"messages": [*remove_draft, *cleanup, AIMessage(SAFE_ANSWER, id=f"safe-{uuid4().hex}")]}
        note = HumanMessage(result.feedback(), id=f"{FEEDBACK_ID_PREFIX}{uuid4().hex}")
        return {"messages": [*remove_draft, note], "answer_revisions": revisions + 1, "jump_to": "model"}
