"""Tools Wasalt: cari iklan + statistik harga dari tabel `wasalt_listings` (query SQL, tanpa pandas).

Logika murni (validasi input, susun query, statistik) dipisah dari akses DB supaya bisa di-unit-test.
Harga sewa selalu dibandingkan per tahun (`rent_yearly_sar`); harga jual memakai `price_sar`.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from typing import Any, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field, field_validator

from agent.db import fetch_all

SOURCE = "Wasalt"
SNAPSHOT_NOTE = (
    "Wasalt listings are a snapshot taken on {date}; availability and prices may have changed. "
    "Check wasalt.sa for current listings."
)
SMALL_SAMPLE = 5
IQR_K = 1.5

CITIES = ("Riyadh", "Jeddah", "Makkah", "Khobar", "Dammam", "Madinah", "Abha", "Taif", "Khamis Mushait", "Buraydah")
PROPERTY_TYPES = ("Apartment", "Villa", "Land", "Building", "Floor", "Rest House")
PURPOSES = ("sale", "rent")

City = Literal[CITIES]  # type: ignore[valid-type]
PropertyType = Literal[PROPERTY_TYPES]  # type: ignore[valid-type]
Purpose = Literal[PURPOSES]  # type: ignore[valid-type]

CITY_ALIASES = {
    "riyad": "Riyadh", "ar riyadh": "Riyadh", "jedda": "Jeddah", "jiddah": "Jeddah", "mecca": "Makkah",
    "makka": "Makkah", "al khobar": "Khobar", "alkhobar": "Khobar", "al-khobar": "Khobar", "medina": "Madinah",
    "al madinah": "Madinah", "madina": "Madinah", "ad dammam": "Dammam", "at taif": "Taif", "al taif": "Taif",
    "khamis": "Khamis Mushait", "buraidah": "Buraydah", "buraida": "Buraydah",
}
TYPE_ALIASES = {
    "apartments": "Apartment", "flat": "Apartment", "flats": "Apartment", "villas": "Villa", "house": "Villa",
    "houses": "Villa", "lands": "Land", "plot": "Land", "plots": "Land", "buildings": "Building",
    "floors": "Floor", "resthouse": "Rest House", "rest houses": "Rest House", "istiraha": "Rest House",
}
PURPOSE_ALIASES = {"buy": "sale", "for sale": "sale", "sell": "sale", "purchase": "sale", "for rent": "rent",
                   "lease": "rent", "rental": "rent", "renting": "rent"}


def normalize_choice(value: Any, choices: tuple[str, ...], aliases: dict[str, str]) -> Any:
    """Samakan input LLM ke nilai baku (huruf besar/kecil, alias).

    Nilai asing dibiarkan → error Pydantic yang menyebut daftar nilai valid, lalu agent mencoba lagi.
    """
    if not isinstance(value, str):
        return value
    key = " ".join(value.strip().lower().split())
    for choice in choices:
        if choice.lower() == key:
            return choice
    return aliases.get(key, value)


class _WasaltBase(BaseModel):
    city: City = Field(description="Saudi city.")
    purpose: Purpose = Field(description="'sale' (buy) or 'rent'.")

    @field_validator("city", mode="before")
    @classmethod
    def _city(cls, v: Any) -> Any:
        return normalize_choice(v, CITIES, CITY_ALIASES)

    @field_validator("purpose", mode="before")
    @classmethod
    def _purpose(cls, v: Any) -> Any:
        return normalize_choice(v, PURPOSES, PURPOSE_ALIASES)

    @field_validator("property_type", mode="before", check_fields=False)
    @classmethod
    def _ptype(cls, v: Any) -> Any:
        return normalize_choice(v, PROPERTY_TYPES, TYPE_ALIASES)


class ListingSearch(_WasaltBase):
    """Filter iklan Wasalt."""

    property_type: PropertyType | None = Field(default=None, description="Property type; omit for all types.")
    min_price: float | None = Field(default=None, ge=0, description="Minimum price in SAR (yearly rent if purpose=rent).")
    max_price: float | None = Field(default=None, ge=0, description="Maximum price in SAR (yearly rent if purpose=rent).")
    bedrooms: int | None = Field(default=None, ge=0, le=20, description="Exact number of bedrooms.")
    district: str | None = Field(default=None, max_length=60, description="District name (partial match).")
    sort: Literal["price_asc", "price_desc", "newest"] = Field(default="price_asc")
    limit: int = Field(default=5, ge=1, le=10, description="How many listings to return (max 10).")


class MarketStatsQuery(_WasaltBase):
    """Filter statistik pasar Wasalt."""

    property_type: PropertyType | None = Field(
        default=None, description="Property type; omit to get stats for every type in the city."
    )
    bedrooms: int | None = Field(default=None, ge=0, le=20, description="Exact number of bedrooms.")


def price_column(purpose: str) -> str:
    """Kolom harga yang dibandingkan: sewa per tahun, atau harga jual."""
    return "rent_yearly_sar" if purpose == "rent" else "price_sar"


def build_where(q: ListingSearch | MarketStatsQuery) -> tuple[str, dict[str, Any]]:
    """Susun klausa WHERE + parameter (selalu parameterized, tanpa string user di SQL)."""
    price = price_column(q.purpose)
    clauses = ["city = %(city)s", "purpose = %(purpose)s", f"{price} IS NOT NULL"]
    params: dict[str, Any] = {"city": q.city, "purpose": q.purpose}
    if q.property_type:
        clauses.append("property_type = %(property_type)s")
        params["property_type"] = q.property_type
    if q.bedrooms is not None:
        clauses.append("bedrooms = %(bedrooms)s")
        params["bedrooms"] = q.bedrooms
    if isinstance(q, ListingSearch):
        if q.min_price is not None:
            clauses.append(f"{price} >= %(min_price)s")
            params["min_price"] = q.min_price
        if q.max_price is not None:
            clauses.append(f"{price} <= %(max_price)s")
            params["max_price"] = q.max_price
        if q.district:
            clauses.append("district ILIKE %(district)s")
            params["district"] = f"%{q.district.strip()}%"
    return " AND ".join(clauses), params


ORDER_BY = {
    "price_asc": "{price} ASC, id",
    "price_desc": "{price} DESC, id",
    "newest": "published_at DESC NULLS LAST, id",
}


def build_listing_query(q: ListingSearch) -> tuple[str, dict[str, Any]]:
    """Query cari iklan; `total_matches` = jumlah semua yang cocok (sebelum LIMIT)."""
    where, params = build_where(q)
    order = ORDER_BY[q.sort].format(price=price_column(q.purpose))
    sql = (
        "SELECT id, title, city, district, property_type, purpose, price_sar, rent_period, rent_yearly_sar, "
        "bedrooms, bathrooms, area_sqm, price_per_sqm, furnishing, url, snapshot_date, "
        "count(*) OVER () AS total_matches "
        f"FROM wasalt_listings WHERE {where} ORDER BY {order} LIMIT %(limit)s"
    )
    return sql, {**params, "limit": q.limit}


def build_stats_query(q: MarketStatsQuery) -> tuple[str, dict[str, Any]]:
    """Query ambil harga mentah per tipe; statistik dihitung di Python (`price_stats`)."""
    where, params = build_where(q)
    price = price_column(q.purpose)
    sql = (
        f"SELECT property_type, {price} AS price, price_per_sqm, snapshot_date "
        f"FROM wasalt_listings WHERE {where}"
    )
    return sql, params


def _clean_number(x: Any) -> Any:
    if isinstance(x, float):
        return round(x, 2) if not x.is_integer() else int(x)
    return x


def format_listing(row: dict[str, Any]) -> dict[str, Any]:
    """Satu iklan untuk agent: buang kolom kosong & internal."""
    skip = {"total_matches", "snapshot_date"}
    out = {k: _clean_number(v) for k, v in row.items() if k not in skip and v not in (None, "")}
    if row.get("purpose") == "sale":
        out.pop("rent_period", None)
        out.pop("rent_yearly_sar", None)
    return out


def iqr_filter(values: list[float], k: float = IQR_K) -> list[float]:
    """Buang outlier dengan IQR di skala log (harga properti miring ke kanan). < 4 nilai: tidak dibuang."""
    positive = [v for v in values if v > 0]
    if len(positive) < 4:
        return positive
    logs = [math.log(v) for v in positive]
    q1, _, q3 = statistics.quantiles(logs, n=4, method="inclusive")
    lo, hi = q1 - k * (q3 - q1), q3 + k * (q3 - q1)
    return [v for v, lv in zip(positive, logs, strict=True) if lo <= lv <= hi]


def percentile(sorted_values: list[float], p: float) -> float:
    """Persentil interpolasi linear (sama dengan `percentile_cont` Postgres)."""
    if not sorted_values:
        raise ValueError("empty")
    pos = (len(sorted_values) - 1) * p
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def price_stats(prices: list[float], prices_per_sqm: list[float]) -> dict[str, Any]:
    """Statistik satu grup: n, median, p25–p75, min–max (setelah buang outlier), median harga/m²."""
    kept = sorted(iqr_filter(prices))
    if not kept:
        return {"n": 0}
    ppsqm = sorted(iqr_filter(prices_per_sqm))
    stats: dict[str, Any] = {
        "n": len(kept),
        "outliers_removed": len([p for p in prices if p > 0]) - len(kept),
        "median": round(percentile(kept, 0.5)),
        "p25": round(percentile(kept, 0.25)),
        "p75": round(percentile(kept, 0.75)),
        "min": round(kept[0]),
        "max": round(kept[-1]),
        "median_price_per_sqm": round(percentile(ppsqm, 0.5)) if ppsqm else None,
        "small_sample": len(kept) < SMALL_SAMPLE,
    }
    return {k: v for k, v in stats.items() if v is not None}


def group_stats(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Statistik per tipe properti, urut dari sampel terbanyak."""
    prices: dict[str, list[float]] = defaultdict(list)
    ppsqm: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        prices[r["property_type"]].append(float(r["price"]))
        if r.get("price_per_sqm"):
            ppsqm[r["property_type"]].append(float(r["price_per_sqm"]))
    groups = [{"property_type": t, **price_stats(v, ppsqm[t])} for t, v in prices.items()]
    return sorted(groups, key=lambda g: -g["n"])


def snapshot_note(rows: list[dict[str, Any]]) -> str:
    """Catatan tanggal snapshot Wasalt untuk agent."""
    dates = sorted({str(r["snapshot_date"]) for r in rows if r.get("snapshot_date")})
    return SNAPSHOT_NOTE.format(date=dates[-1] if dates else "unknown date")


def price_basis(purpose: str) -> str:
    """Penjelasan arti angka harga (sewa per tahun / harga jual)."""
    return "yearly rent in SAR" if purpose == "rent" else "total sale price in SAR"


def dumps(data: dict[str, Any]) -> str:
    """JSON untuk hasil tool."""
    return json.dumps(data, ensure_ascii=False, default=str)


@tool("search_wasalt_listings", args_schema=ListingSearch)
async def search_wasalt_listings(**kwargs: Any) -> str:
    """Search Saudi property listings from Wasalt (wasalt.sa) by city, sale/rent, type, price and bedrooms.

    Use for questions about specific listings available on the Saudi market. These are Wasalt ads,
    NOT DarGlobal projects. Prices are SAR; for rent, prices are per year.
    """
    q = ListingSearch(**kwargs)
    sql, params = build_listing_query(q)
    rows = await fetch_all(sql, params)
    filters = q.model_dump(exclude_none=True, exclude={"sort", "limit"})
    if not rows:
        return dumps({"source": SOURCE, "status": "not_found", "filters": filters,
                      "hint": "No listing matches. Try relaxing price, bedrooms or district."})
    return dumps({
        "source": SOURCE,
        "status": "ok",
        "filters": filters,
        "price_basis": price_basis(q.purpose),
        "total_matches": rows[0]["total_matches"],
        "returned": len(rows),
        "listings": [format_listing(r) for r in rows],
        "note": snapshot_note(rows),
    })


@tool("wasalt_market_stats", args_schema=MarketStatsQuery)
async def wasalt_market_stats(**kwargs: Any) -> str:
    """Price statistics of Wasalt listings in a Saudi city.

    Returns median, p25-p75 range, min-max, median price per square meter and sample size n
    (outliers removed with IQR).

    Use for "typical price", "average rent", or market comparison questions. Always report n;
    small_sample=true means fewer than 5 listings, so say the estimate is weak.
    """
    q = MarketStatsQuery(**kwargs)
    sql, params = build_stats_query(q)
    rows = await fetch_all(sql, params)
    filters = q.model_dump(exclude_none=True)
    if not rows:
        return dumps({"source": SOURCE, "status": "not_found", "filters": filters,
                      "hint": "No Wasalt listing for this filter. Try without property_type or bedrooms."})
    return dumps({
        "source": SOURCE,
        "status": "ok",
        "filters": filters,
        "price_basis": price_basis(q.purpose),
        "groups": group_stats(rows),
        "note": snapshot_note(rows),
    })
