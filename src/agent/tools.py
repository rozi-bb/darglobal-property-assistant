"""Tools agent (lihat docs/architecture.md bagian Tools).

- `search_darglobal`        : hybrid search halaman DarGlobal (agent/retrieval.py)
- `search_wasalt_listings`  : filter iklan Wasalt (agent/wasalt.py)
- `wasalt_market_stats`     : statistik harga Wasalt (agent/wasalt.py)
- `ask_user`                : tanya balik ke user lewat `interrupt` (butuh checkpointer)

Semua hasil tool membawa field `source` supaya sumber DarGlobal dan Wasalt tidak tercampur.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from langchain_core.tools import BaseTool, tool
from langgraph.types import interrupt
from pydantic import BaseModel, Field, field_validator

from agent import retrieval
from agent.db import fetch_all
from agent.wasalt import normalize_choice, search_wasalt_listings, wasalt_market_stats

DARGLOBAL = "DarGlobal"
COUNTRIES = ("United Arab Emirates", "Saudi Arabia", "Oman", "Qatar", "Spain", "United Kingdom")
COUNTRY_ALIASES = {
    "uae": "United Arab Emirates", "emirates": "United Arab Emirates", "dubai": "United Arab Emirates",
    "abu dhabi": "United Arab Emirates", "ksa": "Saudi Arabia", "saudi": "Saudi Arabia", "uk": "United Kingdom",
    "england": "United Kingdom", "london": "United Kingdom", "great britain": "United Kingdom",
    "britain": "United Kingdom", "muscat": "Oman", "doha": "Qatar", "marbella": "Spain",
}
Country = Literal[COUNTRIES]  # type: ignore[valid-type]


class DarGlobalSearch(BaseModel):
    """Input `search_darglobal`."""

    query: str = Field(
        min_length=2, max_length=300,
        description="Standalone English search query, e.g. 'Trump Tower Jeddah payment plan'. "
                    "Resolve pronouns from the conversation first.",
    )
    country: Country | None = Field(
        default=None, description="Only results about DarGlobal in this country. Also lists all projects there."
    )

    @field_validator("country", mode="before")
    @classmethod
    def _country(cls, v: Any) -> Any:
        return normalize_choice(v, COUNTRIES, COUNTRY_ALIASES)


async def projects_in_country(country: str) -> list[dict[str, Any]]:
    """Daftar lengkap proyek DarGlobal di satu negara (tabel `darglobal_projects`)."""
    rows = await fetch_all(
        "SELECT title, location, status, property_type, url FROM darglobal_projects "
        "WHERE country = %(country)s ORDER BY title",
        {"country": country},
    )
    return [{k: v for k, v in r.items() if v} for r in rows]


def format_hit(chunk: retrieval.Chunk, score: float | None) -> dict[str, Any]:
    """Satu hasil pencarian untuk agent."""
    hit: dict[str, Any] = {
        "title": chunk.title,
        "section": chunk.section,
        "kind": chunk.kind,
        "url": chunk.url,
        "text": chunk.text,
        "captured_at": chunk.captured_at,
    }
    if score is not None:
        hit["relevance"] = round(score, 2)
    return hit


@tool("search_darglobal", args_schema=DarGlobalSearch)
async def search_darglobal(query: str, country: str | None = None) -> str:
    """Search DarGlobal's website content (projects, blog, press, company pages).

    Use for facts about DarGlobal projects: location, unit types, status, amenities, brand partners,
    investment highlights.

    Returns up to 4 passages with their URL. Use `country` for "which projects are in X" questions:
    the result then also lists every DarGlobal project in that country. Unit prices are usually not published.
    """
    result = await retrieval.hybrid_search(query, country)
    payload: dict[str, Any] = {"source": DARGLOBAL, "query": query}
    if country:
        payload["country"] = country
        payload["projects_in_country"] = await projects_in_country(country)
    if not result.hits:
        payload.update(status="not_found",
                       hint="No relevant DarGlobal page. Try another query, or say the information is not available.")
    else:
        payload.update(status="ok", reranked=result.reranked,
                       results=[format_hit(c, s) for c, s in result.hits])
    return json.dumps(payload, ensure_ascii=False)


class AskUser(BaseModel):
    """Input `ask_user`."""

    question: str = Field(min_length=3, max_length=300, description="Short clarifying question for the user.")
    options: list[str] = Field(
        min_length=2, max_length=5,
        description="2-5 short answer choices shown as buttons (the user may also type a free answer).",
    )


@tool("ask_user", args_schema=AskUser)
def ask_user(question: str, options: list[str]) -> str:
    """Ask the user a clarifying question with button choices and wait for the answer.

    Use when the request is ambiguous (e.g. no city, or unclear whether they want to buy or rent).
    """
    answer = interrupt({"type": "ask_user", "question": question, "options": [o[:80] for o in options]})
    return json.dumps({"question": question, "user_answer": str(answer)}, ensure_ascii=False)


DATA_TOOLS: list[BaseTool] = [search_darglobal, search_wasalt_listings, wasalt_market_stats]
TOOLS: list[BaseTool] = [*DATA_TOOLS, ask_user]
