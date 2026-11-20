import json

import pytest
from pydantic import ValidationError

from agent import retrieval, tools
from agent.retrieval import (
    Chunk,
    HybridIndex,
    SearchResult,
    dedupe,
    matches_country,
    rrf,
    select_top,
)
from agent.tools import DarGlobalSearch

pytestmark = pytest.mark.anyio


def chunk(cid: str, text: str, kind: str = "blog", country: str | None = None, title: str = "T") -> Chunk:
    return Chunk(chunk_id=cid, url=f"https://darglobal.co.uk/{cid}", kind=kind, title=title, section="S",
                 text=f"# {title}\n\n{text}", country=country)


def test_rrf_rewards_items_ranked_by_both_retrievers() -> None:
    fused = [cid for cid, _ in rrf([["a", "b", "c"], ["c", "a", "d"]])]
    assert fused[:2] == ["a", "c"]
    assert set(fused) == {"a", "b", "c", "d"}


def test_bm25_prefers_rare_project_name() -> None:
    index = HybridIndex([
        chunk("1", "Luxury villas with sea view and golf course."),
        chunk("2", "Rayana is a new community in Riyadh with villas."),
        chunk("3", "Villas villas villas in Dubai."),
    ])
    assert index.bm25_ranking("Rayana villas")[0] == "2"


def test_bm25_respects_allowed_ids() -> None:
    index = HybridIndex([chunk("1", "Trump Tower Jeddah"), chunk("2", "Trump Tower Dubai"),
                         chunk("3", "Golf villas in Oman"), chunk("4", "Marbella beach homes")])
    assert index.bm25_ranking("Jeddah Dubai", allowed={"2"}) == ["2"]


def test_dedupe_collapses_same_body() -> None:
    by_id = {"a": chunk("a", "Shared AIDA text", title="X"), "b": chunk("b", "Shared AIDA text", title="Y"),
             "c": chunk("c", "Other")}
    assert dedupe(["a", "b", "c"], by_id) == ["a", "c"]


def test_matches_country_for_project_and_blog() -> None:
    assert matches_country(chunk("p", "x", kind="project", country="Oman"), "Oman")
    assert not matches_country(chunk("p", "x", kind="project", country="Oman"), "Spain")
    assert matches_country(chunk("b", "New tower in Riyadh"), "Saudi Arabia")
    assert not matches_country(chunk("b", "Marbella villas"), "Saudi Arabia")


def test_select_top_with_and_without_rerank() -> None:
    cands = [chunk(str(i), "t") for i in range(6)]
    assert [c.chunk_id for c, s in select_top(cands, None)] == ["0", "1", "2", "3"]
    picked = select_top(cands, [0.1, 0.9, 0.6, 0.2, 0.55, 0.3])
    assert [c.chunk_id for c, _ in picked] == ["1", "2", "4"]
    assert select_top(cands, [0.1] * 6) == []


async def test_rerank_without_key_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert await retrieval.rerank("q", [chunk("1", "t")]) is None


def test_country_aliases() -> None:
    assert DarGlobalSearch(query="projects", country="ksa").country == "Saudi Arabia"
    with pytest.raises(ValidationError):
        DarGlobalSearch(query="projects", country="France")


async def test_search_darglobal_output_structure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_search(query: str, country: str | None) -> SearchResult:
        return SearchResult(hits=[(chunk("p1", "Tower in Jeddah", kind="project"), 0.8)], reranked=True)

    async def fake_projects(country: str) -> list[dict]:
        return [{"title": "Trump Tower Jeddah", "url": "https://darglobal.co.uk/trump-tower-jeddah"}]

    monkeypatch.setattr(retrieval, "hybrid_search", fake_search)
    monkeypatch.setattr(tools, "projects_in_country", fake_projects)
    out = json.loads(await tools.search_darglobal.ainvoke({"query": "Jeddah tower", "country": "saudi"}))
    assert out["source"] == "DarGlobal" and out["status"] == "ok" and out["reranked"] is True
    assert set(out["results"][0]) >= {"title", "url", "text", "relevance"}
    assert out["projects_in_country"][0]["title"] == "Trump Tower Jeddah"


async def test_search_darglobal_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_search(query: str, country: str | None) -> SearchResult:
        return SearchResult(hits=[], reranked=True)

    monkeypatch.setattr(retrieval, "hybrid_search", fake_search)
    out = json.loads(await tools.search_darglobal.ainvoke({"query": "moon base"}))
    assert out["status"] == "not_found" and "results" not in out


@pytest.mark.integration
async def test_hybrid_search_against_local_db() -> None:
    """Butuh Postgres lokal + `dataprep.embed` sudah dijalankan."""
    result = await retrieval.hybrid_search("Trump Tower Jeddah")
    assert result.hits
    assert any("jeddah" in c.url.lower() for c, _ in result.hits)
