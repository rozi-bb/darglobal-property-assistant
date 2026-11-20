import json

import pytest
from pydantic import ValidationError

from agent import wasalt
from agent.wasalt import (
    ListingSearch,
    MarketStatsQuery,
    build_listing_query,
    build_stats_query,
    format_listing,
    group_stats,
    iqr_filter,
    percentile,
    price_stats,
)

pytestmark = pytest.mark.anyio


def test_input_normalizes_case_and_aliases() -> None:
    q = ListingSearch(city="al khobar", purpose="for rent", property_type="flat")
    assert (q.city, q.purpose, q.property_type) == ("Khobar", "rent", "Apartment")


def test_invalid_city_error_lists_valid_values() -> None:
    with pytest.raises(ValidationError) as err:
        ListingSearch(city="Dubai", purpose="sale")
    assert "Riyadh" in str(err.value)


def test_limit_is_capped() -> None:
    with pytest.raises(ValidationError):
        ListingSearch(city="Riyadh", purpose="sale", limit=50)


def test_listing_query_uses_yearly_rent_and_params() -> None:
    q = ListingSearch(city="Riyadh", purpose="rent", property_type="Apartment", max_price=80000, bedrooms=3)
    sql, params = build_listing_query(q)
    assert "rent_yearly_sar <= %(max_price)s" in sql
    assert "bedrooms = %(bedrooms)s" in sql
    assert params == {"city": "Riyadh", "purpose": "rent", "property_type": "Apartment",
                      "bedrooms": 3, "max_price": 80000, "limit": 5}
    assert "Riyadh" not in sql  # nilai user hanya lewat parameter


def test_sale_query_uses_sale_price() -> None:
    sql, _ = build_listing_query(ListingSearch(city="Jeddah", purpose="sale", min_price=1))
    assert "price_sar >= %(min_price)s" in sql
    assert "rent_yearly_sar" not in sql.split("FROM")[1]


def test_stats_query_has_no_price_filter() -> None:
    sql, params = build_stats_query(MarketStatsQuery(city="Jeddah", purpose="sale", property_type="Villa"))
    assert "price_sar AS price" in sql
    assert set(params) == {"city", "purpose", "property_type"}


def test_iqr_filter_removes_extreme_values() -> None:
    values = [100.0, 110, 120, 125, 130, 140, 150, 100_000]
    kept = iqr_filter(values)
    assert 100_000 not in kept
    assert len(kept) == 7


def test_iqr_filter_keeps_small_groups() -> None:
    assert iqr_filter([1.0, 1000.0, 5.0]) == [1.0, 1000.0, 5.0]


def test_percentile_matches_linear_interpolation() -> None:
    assert percentile([1.0, 2, 3, 4], 0.5) == 2.5
    assert percentile([10.0], 0.25) == 10.0


def test_price_stats_structure_and_small_sample_flag() -> None:
    s = price_stats([100.0, 200, 300], [10.0, 20])
    assert set(s) >= {"n", "median", "p25", "p75", "min", "max", "small_sample", "median_price_per_sqm"}
    assert s["n"] == 3 and s["small_sample"] is True
    assert s["p25"] <= s["median"] <= s["p75"]


def test_group_stats_sorted_by_sample_size() -> None:
    rows = [{"property_type": "Villa", "price": 1.0, "price_per_sqm": None}] + [
        {"property_type": "Apartment", "price": float(p), "price_per_sqm": 5.0} for p in (1, 2, 3)
    ]
    groups = group_stats(rows)
    assert [g["property_type"] for g in groups] == ["Apartment", "Villa"]


def test_format_listing_drops_rent_fields_for_sale() -> None:
    row = {"id": 1, "purpose": "sale", "price_sar": 5e5, "rent_period": None, "rent_yearly_sar": None,
           "district": "", "total_matches": 9, "url": "https://wasalt.sa/x"}
    out = format_listing(row)
    assert out == {"id": 1, "purpose": "sale", "price_sar": 500000, "url": "https://wasalt.sa/x"}


async def test_search_tool_returns_source_and_listings(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(sql: str, params: dict) -> list[dict]:
        return [{"id": 7, "title": "Villa", "city": "Jeddah", "purpose": "sale", "property_type": "Villa",
                 "price_sar": 2e6, "url": "https://wasalt.sa/v7", "snapshot_date": "2026-11-20",
                 "total_matches": 12}]

    monkeypatch.setattr(wasalt, "fetch_all", fake_fetch)
    out = json.loads(await wasalt.search_wasalt_listings.ainvoke({"city": "Jeddah", "purpose": "sale"}))
    assert out["source"] == "Wasalt" and out["status"] == "ok"
    assert out["total_matches"] == 12 and len(out["listings"]) == 1
    assert "2026-11-20" in out["note"]


async def test_stats_tool_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(sql: str, params: dict) -> list[dict]:
        return []

    monkeypatch.setattr(wasalt, "fetch_all", fake_fetch)
    out = json.loads(await wasalt.wasalt_market_stats.ainvoke({"city": "Abha", "purpose": "rent"}))
    assert out == {"source": "Wasalt", "status": "not_found", "filters": {"city": "Abha", "purpose": "rent"},
                   "hint": out["hint"]}


@pytest.mark.integration
async def test_tools_against_local_db() -> None:
    """Butuh Postgres lokal berisi data (`docker compose up -d db` + `dataprep.load_db`)."""
    out = json.loads(await wasalt.search_wasalt_listings.ainvoke(
        {"city": "Riyadh", "purpose": "rent", "property_type": "Apartment", "max_price": 80000}))
    assert out["status"] == "ok"
    assert all(item["rent_yearly_sar"] <= 80000 for item in out["listings"])
    stats = json.loads(await wasalt.wasalt_market_stats.ainvoke({"city": "Jeddah", "purpose": "sale"}))
    assert stats["groups"] and all(g["n"] > 0 for g in stats["groups"])
