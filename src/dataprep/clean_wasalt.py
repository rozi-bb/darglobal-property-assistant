"""Bersihkan listing Wasalt (snapshot 19–20 Nov 2026) jadi tabel `wasalt_listings` yang rapi.

Aturan llms.txt Wasalt: atribusi "Source: Wasalt.sa", tanpa kontak pengiklan, arahkan user ke situs live.
"""

from __future__ import annotations

import math
import re
import statistics
from typing import Any, Literal

from pydantic import BaseModel, Field

SOURCE = "Wasalt"

# Nama kota di data mentah → nama umum (English).
CITY_MAP = {
    "aldammam": "Dammam",
    "alttayif": "Taif",
    "makkah al mukarramah": "Makkah",
    "bariduh": "Buraydah",
    "khobar": "Khobar",
    "madinah": "Madinah",
}

# Batas wajar harga (SAR). Sewa dinormalkan ke per tahun.
SALE_BOUNDS = (100_000, 200_000_000)
RENT_YEARLY_BOUNDS = (6_000, 5_000_000)
IQR_K = 3.0  # di skala log; longgar, hanya buang yang jelas salah input
# Jumlah kamar tidak masuk akal → dikosongkan (iklannya tetap disimpan).
MAX_APARTMENT_BEDROOMS = 10
MIN_SQM_PER_BEDROOM = 15.0


class Listing(BaseModel):
    """Satu iklan Wasalt yang sudah dibersihkan (bukan proyek DarGlobal)."""

    id: int
    purpose: Literal["sale", "rent"]
    title: str
    property_type: str
    main_type: str | None = None
    city: str
    district: str | None = None
    address: str | None = None
    price_sar: float
    rent_period: Literal["yearly", "monthly"] | None = None
    rent_yearly_sar: float | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    area_sqm: float | None = None
    price_per_sqm: float | None = None
    age_years: int | None = None
    furnishing: str | None = None
    amenities: list[str] = Field(default_factory=list)
    lat: float | None = None
    lon: float | None = None
    published_at: str | None = None
    url: str
    snapshot_date: str = "2026-11-20"
    source: str = SOURCE


def norm_city(city: str | None) -> str:
    """Seragamkan nama kota (mis. 'Aldammam' → 'Dammam')."""
    raw = (city or "").strip()
    return CITY_MAP.get(raw.lower(), raw.title() if raw.islower() else raw) or "Unknown"


def to_float(value: Any) -> float | None:
    """Angka dari int/float/string ('450', '1,200.5'); None kalau kosong/tidak valid/≤0."""
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(str(value).replace(",", "").strip())
    except ValueError:
        return None
    return num if math.isfinite(num) and num > 0 else None


def to_int(value: Any) -> int | None:
    """Bilangan bulat ≥ 0 atau None."""
    num = to_float(value)
    return int(num) if num is not None else (0 if value == 0 else None)


def parse_age(value: Any) -> int | None:
    """Ubah umur bangunan: 'New' → 0, angka → tahun."""
    if isinstance(value, str) and value.strip().lower() == "new":
        return 0
    return to_int(value)


def parse_rent(price: float, rent_freq: dict[str, Any] | None) -> tuple[str | None, float | None]:
    """Normalisasi sewa → (periode, sewa per tahun).

    Data mentah: kalau ada kunci 'monthly', harga = per bulan (kunci 'yearly' ikut berisi angka sama).
    """
    freq = rent_freq or {}
    if "monthly" in freq:
        return "monthly", price * 12
    return "yearly", price


def valid_coord(lat: float | None, lon: float | None) -> tuple[float | None, float | None]:
    """Koordinat hanya dipakai kalau di wilayah Arab Saudi (kasar)."""
    if lat is None or lon is None or not (16 <= lat <= 33 and 34 <= lon <= 56):
        return None, None
    return lat, lon


def sane_bedrooms(bedrooms: int | None, property_type: str, area: float | None) -> int | None:
    """Jumlah kamar, atau None kalau tidak masuk akal (apartemen > 10 kamar, atau < 15 m² per kamar)."""
    if not bedrooms:
        return bedrooms
    if property_type == "Apartment" and bedrooms > MAX_APARTMENT_BEDROOMS:
        return None
    if area is not None and area / bedrooms < MIN_SQM_PER_BEDROOM:
        return None
    return bedrooms


def clean_row(row: dict[str, Any]) -> tuple[Listing | None, str | None]:
    """Satu baris mentah → (Listing, None) atau (None, alasan ditolak)."""
    price = to_float(row.get("price"))
    if price is None:
        return None, "harga kosong/0"
    purpose = row.get("purpose")
    rent_period, rent_yearly = (None, None)
    if purpose == "rent":
        rent_period, rent_yearly = parse_rent(price, row.get("rent_freq"))
        if not (RENT_YEARLY_BOUNDS[0] <= rent_yearly <= RENT_YEARLY_BOUNDS[1]):
            return None, f"sewa/tahun di luar batas wajar ({rent_yearly:,.0f} SAR)"
    elif purpose == "sale":
        if not (SALE_BOUNDS[0] <= price <= SALE_BOUNDS[1]):
            return None, f"harga jual di luar batas wajar ({price:,.0f} SAR)"
    else:
        return None, "purpose tidak dikenal"
    area = to_float(row.get("area_sqm"))
    if area is not None and not (10 <= area <= 1_000_000):
        area = None
    lat, lon = valid_coord(to_float(row.get("lat")), to_float(row.get("lon")))
    yearly_or_price = rent_yearly if purpose == "rent" else price
    property_type = row.get("type") or "Unknown"
    listing = Listing(
        id=int(row["id"]),
        purpose=purpose,
        title=re.sub(r"\s+", " ", str(row.get("title") or row.get("type") or "")).strip(),
        property_type=property_type,
        main_type=row.get("main_type"),
        city=norm_city(row.get("city")),
        district=(row.get("district") or "").strip() or None,
        address=(row.get("address") or "").strip() or None,
        price_sar=price,
        rent_period=rent_period,
        rent_yearly_sar=rent_yearly,
        bedrooms=sane_bedrooms(to_int(row.get("bedrooms")), property_type, area),
        bathrooms=to_int(row.get("bathrooms")),
        area_sqm=area,
        price_per_sqm=round(yearly_or_price / area, 2) if area and yearly_or_price else None,
        age_years=parse_age(row.get("age")),
        furnishing=row.get("furnishing"),
        amenities=[str(a) for a in row.get("amenities") or []],
        lat=lat,
        lon=lon,
        published_at=row.get("published"),
        url=row["url"],
    )
    return listing, None


def _log_bounds(values: list[float]) -> tuple[float, float] | None:
    """Batas bawah/atas IQR di skala log; None kalau data terlalu sedikit."""
    if len(values) < 8:
        return None
    logs = sorted(math.log(v) for v in values)
    q1, _, q3 = statistics.quantiles(logs, n=4)
    return q1 - IQR_K * (q3 - q1), q3 + IQR_K * (q3 - q1)


def _yearly_price(item: Listing) -> float:
    return item.rent_yearly_sar if item.purpose == "rent" and item.rent_yearly_sar else item.price_sar


def iqr_outliers(listings: list[Listing]) -> tuple[dict[int, str], set[int]]:
    """Cek harga per (purpose, tipe) di skala log.

    Kembalikan ({id: alasan} untuk harga ekstrem → ditolak, {id} yang harganya wajar tapi harga/m² ekstrem
    → luas dianggap salah input, dikosongkan).
    """
    groups: dict[tuple[str, str], list[Listing]] = {}
    for item in listings:
        groups.setdefault((item.purpose, item.property_type), []).append(item)
    reject: dict[int, str] = {}
    bad_area: set[int] = set()
    for (purpose, ptype), items in groups.items():
        price_b = _log_bounds([_yearly_price(i) for i in items])
        ppsqm_b = _log_bounds([i.price_per_sqm for i in items if i.price_per_sqm])
        for i in items:
            price_ok = price_b is None or price_b[0] <= math.log(_yearly_price(i)) <= price_b[1]
            if not price_ok:
                reject[i.id] = f"harga ekstrem untuk {purpose} {ptype} ({_yearly_price(i):,.0f} SAR)"
            elif ppsqm_b and i.price_per_sqm and not ppsqm_b[0] <= math.log(i.price_per_sqm) <= ppsqm_b[1]:
                bad_area.add(i.id)
    return reject, bad_area


def clean_all(rows: list[dict[str, Any]]) -> tuple[list[Listing], list[dict[str, Any]]]:
    """Bersihkan semua baris: validasi, dedup (id & konten sama), buang outlier. Kembalikan (bersih, ditolak)."""
    rejected: list[dict[str, Any]] = []
    kept: list[Listing] = []
    seen_ids: set[int] = set()
    seen_content: set[tuple[Any, ...]] = set()
    for row in rows:
        listing, reason = clean_row(row)
        if listing is None:
            rejected.append({"id": row.get("id"), "url": row.get("url"), "reason": reason})
            continue
        sig = (listing.purpose, listing.title.lower(), listing.price_sar, listing.district, listing.area_sqm, listing.bedrooms)
        if listing.id in seen_ids or sig in seen_content:
            rejected.append({"id": listing.id, "url": listing.url, "reason": "duplikat"})
            continue
        seen_ids.add(listing.id)
        seen_content.add(sig)
        kept.append(listing)
    outliers, bad_area = iqr_outliers(kept)
    rejected += [{"id": i.id, "url": i.url, "reason": outliers[i.id]} for i in kept if i.id in outliers]
    for i in kept:
        if i.id in bad_area:  # harga wajar, luas tidak masuk akal → kosongkan luas saja
            i.area_sqm = None
            i.price_per_sqm = None
    return [i for i in kept if i.id not in outliers], rejected
