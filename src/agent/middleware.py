"""Middleware custom: prompt dinamis + cek jawaban (link, harga, sumber).

Cek jawaban murni kode (tanpa LLM): draf final yang menyebut link/harga yang tidak ada di hasil tool,
atau tidak menyebut sumber, dikembalikan ke model dengan catatan perbaikan (maks 2 revisi).
Draf yang gagal dihapus dari riwayat, jadi tidak pernah tampil ke user.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urlsplit
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

logger = logging.getLogger(__name__)

MAX_REVISIONS = 2
FEEDBACK_ID_PREFIX = "answer-check-"
CONTACT_URL = "https://darglobal.co.uk/get-in-touch"
# Jawaban hanya boleh menautkan sumber resmi. Teks blog DarGlobal & judul iklan Wasalt (ditulis pengiklan)
# bisa berisi link luar; link itu ada di hasil tool, tapi tetap tidak boleh diteruskan ke user.
ALLOWED_LINK_HOSTS = frozenset({"darglobal.co.uk", "www.darglobal.co.uk", "cdn.darglobal.co.uk",
                                "wasalt.sa", "www.wasalt.sa"})
ALWAYS_ALLOWED_URLS = {CONTACT_URL, "https://darglobal.co.uk", "https://wasalt.sa", "https://www.wasalt.sa"}
SAFE_ANSWER = (
    "I'm sorry, I can't confirm that from the DarGlobal and Wasalt data I have. "
    f"For accurate details, please contact DarGlobal at {CONTACT_URL}."
)
REFUSAL_ANSWER = (
    "Sorry, I can't share my internal instructions or configuration. "
    "I'm happy to help with DarGlobal projects or the Saudi property market."
)
SOURCE_BY_TOOL = {
    "search_darglobal": "DarGlobal",
    "search_wasalt_listings": "Wasalt",
    "wasalt_market_stats": "Wasalt",
}

URL_RE = re.compile(r"https?://[^\s)\]>\"'`*|]+")
_CURRENCY = r"(?:SAR|SR|AED|USD|GBP|EUR|OMR|QAR|riyals?|dirhams?|\$|£|€)"
# Format angka: 1,250,000.5 (Inggris) · 1.250.000,5 (Indonesia/Eropa) · 1,5 (desimal koma) · 1250000.5
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+,\d{1,2}(?!\d)|\d+(?:\.\d+)?"
_MULT = r"(?:\s?(million|mn|bn|billion|thousand|k|m|juta|jt|ribu|rb|miliar)(?![a-z²2]))?"
PRICE_RE = re.compile(rf"{_CURRENCY}\s?({_NUM}){_MULT}|({_NUM}){_MULT}\s?{_CURRENCY}", re.IGNORECASE)
PLAIN_NUM_RE = re.compile(_NUM)
MULTIPLIERS = {
    "k": 1e3, "thousand": 1e3, "ribu": 1e3, "rb": 1e3,
    "m": 1e6, "mn": 1e6, "million": 1e6, "juta": 1e6, "jt": 1e6,
    "bn": 1e9, "billion": 1e9, "miliar": 1e9,
}
THOUSANDS_COMMA_RE = re.compile(r"\d{1,3}(?:,\d{3})+")
THOUSANDS_DOT_RE = re.compile(r"\d{1,3}(?:\.\d{3})+")


@dynamic_prompt
def system_prompt_with_date(request: ModelRequest) -> str:
    """System prompt + tanggal hari ini (agent tahu umur snapshot data)."""
    return f"{SYSTEM_PROMPT}\nToday's date: {date.today().isoformat()}."


# ---------- ekstraksi angka & link ----------


@dataclass(frozen=True)
class Price:
    """Harga yang disebut di teks + toleransi pembulatan sesuai jumlah digit yang ditulis.

    `alternatives`: tafsiran lain (nilai, toleransi) kalau penulisan ambigu, mis. "36.700" = 36700 atau 36,7.
    """

    text: str
    value: float
    tolerance: float
    alternatives: tuple[tuple[float, float], ...] = ()


def normalize_url(url: str) -> str:
    """Samakan URL untuk dibandingkan (tanpa tanda baca/slash di akhir, huruf kecil)."""
    return url.rstrip(".,;:!?").rstrip("/").lower()


def url_host(url: str) -> str:
    """Host URL (huruf kecil, tanpa port)."""
    return urlsplit(normalize_url(url)).hostname or ""


def extract_urls(text: str) -> set[str]:
    """Semua URL di teks (sudah dinormalisasi)."""
    return {normalize_url(u) for u in URL_RE.findall(text)}


def parse_number(num: str) -> list[tuple[float, int]]:
    """Tafsiran angka teks → [(nilai, jumlah desimal)], tafsiran paling mungkin di depan.

    Pemisah ribuan koma (Inggris) dan titik (Indonesia/Eropa) sama-sama diterima. "36.700" ambigu:
    36700 (ribuan) atau 36,7 (desimal), jadi keduanya dikembalikan.
    """
    if "," in num and "." in num:
        decimal_sep = "," if num.rfind(",") > num.rfind(".") else "."
        whole, frac = num.rsplit(decimal_sep, 1)
        return [(float(f"{whole.replace(',', '').replace('.', '')}.{frac}"), len(frac))]
    if "," in num:
        if THOUSANDS_COMMA_RE.fullmatch(num):
            return [(float(num.replace(",", "")), 0)]
        return [(float(num.replace(",", ".")), len(num.split(",")[1]))]
    if "." in num:
        if THOUSANDS_DOT_RE.fullmatch(num):
            thousands = (float(num.replace(".", "")), 0)
            return [thousands] if num.count(".") > 1 else [thousands, (float(num), 3)]
        return [(float(num), len(num.split(".")[1]))]
    return [(float(num), 0)]


def _to_price(num: str, mult: str | None, text: str) -> Price:
    factor = MULTIPLIERS.get((mult or "").lower(), 1.0)
    (value, tolerance), *rest = [(v * factor, 0.5 * 10 ** (-d) * factor) for v, d in parse_number(num)]
    return Price(text=text.strip(), value=value, tolerance=tolerance, alternatives=tuple(rest))


def extract_prices(text: str) -> list[Price]:
    """Angka harga yang ditulis bersama mata uang: 'SAR 1,800,000', '1.8 million SAR', '40k AED'."""
    out = []
    for m in PRICE_RE.finditer(text):
        num, mult = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        out.append(_to_price(num, mult, m.group(0)))
    return out


def extract_numbers(text: str) -> set[float]:
    """Semua angka (termasuk yang berpengali) di teks hasil tool / pesan user."""
    values = {v for n in PLAIN_NUM_RE.findall(text) for v, _ in parse_number(n)}
    for price in extract_prices(text):
        values.add(price.value)
        values.update(v for v, _ in price.alternatives)
    return values


def price_supported(price: Price, known: set[float]) -> bool:
    """Harga dianggap benar kalau sama dengan angka di data (toleransi pembulatan atau 1%)."""
    readings = [(price.value, price.tolerance), *price.alternatives]
    return any(abs(v - k) <= max(tol, 0.01 * abs(k)) for v, tol in readings for k in known)


# ---------- pagar kebocoran (system prompt, nama internal, key) ----------

# Nama internal yang tidak pernah perlu muncul di jawaban untuk user.
INTERNAL_MARKERS = (
    "search_darglobal", "search_wasalt_listings", "wasalt_market_stats", "ask_user", "projects_in_country",
    "Automatic answer check", "## Answer rules", "## Boundaries", "## Tools",
)
SECRET_ENV_NAMES = (
    "LLM_API_KEY", "FALLBACK_LLM_API_KEY", "TYPESAFE_API_KEY", "LANGSMITH_API_KEY", "CHECKPOINT_DATABASE_URL",
    "POSTGRES_PASSWORD",
)
KEY_LIKE_RE = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|lsv2_[A-Za-z0-9_]{20,}|postgres(?:ql)?://\S+:\S+@)")
WORD_RE = re.compile(r"[a-z0-9]+")
LEAK_NGRAM = 8  # jumlah kata berurutan yang dianggap kutipan system prompt
LEAK_MIN_SHARED = 3  # minimal potongan 8 kata yang sama (1-2 bisa kebetulan, mis. kalimat get-in-touch)


def ngrams(text: str, n: int = LEAK_NGRAM) -> set[tuple[str, ...]]:
    """Potongan n kata berurutan (huruf kecil, tanpa tanda baca, tanpa URL: link get-in-touch bukan kebocoran)."""
    words = WORD_RE.findall(URL_RE.sub(" ", text).lower())
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


PROMPT_NGRAMS = ngrams(SYSTEM_PROMPT)


def find_leaks(text: str) -> list[str]:
    """Tanda kebocoran di teks untuk user: kutipan system prompt, nama internal, nama/nilai secret."""
    leaks: list[str] = []
    shared = len(ngrams(text) & PROMPT_NGRAMS)
    if shared >= LEAK_MIN_SHARED:
        leaks.append(f"system prompt ({shared} potongan {LEAK_NGRAM} kata)")
    lowered = text.lower()
    leaks += [m for m in INTERNAL_MARKERS if m.lower() in lowered]
    leaks += [name for name in SECRET_ENV_NAMES if name.lower() in lowered]
    if KEY_LIKE_RE.search(text):
        leaks.append("pola API key / URL database")
    for name in SECRET_ENV_NAMES:
        value = os.environ.get(name, "").strip()
        if len(value) >= 8 and value in text:
            leaks.append(f"nilai {name}")
    return leaks


def user_visible_text(msg: AIMessage) -> str:
    """Teks yang akan dilihat user dari pesan AI.

    Jawaban final: isinya. Pesan pemanggil tool: hanya pertanyaan/pilihan `ask_user` (isi teksnya tidak
    ditampilkan UI; langkah lain tampil sebagai label buatan kode).
    """
    if not msg.tool_calls:
        return answer_text(msg)
    return "\n".join(json.dumps(c["args"], ensure_ascii=False) for c in msg.tool_calls if c["name"] == "ask_user")


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
            lines.append(f"- Remove these links: they are not in any tool result or not on darglobal.co.uk / wasalt.sa. "
                         f"Only use exact DarGlobal or Wasalt links from tool results: {', '.join(self.unknown_urls)}")
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
    result.unknown_urls = sorted({
        u for u in URL_RE.findall(answer)
        if normalize_url(u) not in known_urls or url_host(u) not in ALLOWED_LINK_HOSTS
    })
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
        if not isinstance(draft, AIMessage):
            return None
        cleanup = [RemoveMessage(id=m.id) for m in current_run(messages) if is_feedback(m) and m.id]
        if leaks := find_leaks(user_visible_text(draft)):
            # Bocor tidak direvisi (model bisa bocor lagi): langsung ganti dengan penolakan, tool call dibatalkan.
            logger.warning("Draf diblokir, tanda kebocoran: %s", leaks)
            remove_draft = [RemoveMessage(id=draft.id)] if draft.id else []
            return {"messages": [*remove_draft, *cleanup, AIMessage(REFUSAL_ANSWER, id=f"refusal-{uuid4().hex}")]}
        if draft.tool_calls:
            return None
        result = check_answer(answer_text(draft), messages[:-1])
        if result.ok:
            return {"messages": cleanup} if cleanup else None

        revisions = state.get("answer_revisions", 0)
        remove_draft = [RemoveMessage(id=draft.id)] if draft.id else []
        if revisions >= self.max_revisions:
            return {"messages": [*remove_draft, *cleanup, AIMessage(SAFE_ANSWER, id=f"safe-{uuid4().hex}")]}
        note = HumanMessage(result.feedback(), id=f"{FEEDBACK_ID_PREFIX}{uuid4().hex}")
        return {"messages": [*remove_draft, note], "answer_revisions": revisions + 1, "jump_to": "model"}
