"""Unit test pipeline data (tanpa jaringan/DB)."""

from dataprep.clean_darglobal import (
    classify,
    dedupe_lines,
    is_media,
    iter_texts,
    merge_short,
    old_text_clean,
    page_title,
    page_to_chunks,
    page_to_project,
    split_chunks,
)
from dataprep.clean_wasalt import (
    clean_all,
    clean_row,
    norm_city,
    parse_age,
    sane_bedrooms,
)
from dataprep.common import extract_next_data, parse_sitemap
from dataprep.darglobal import key_to_filename, url_key

MEDIA = {"data": {"id": 1, "attributes": {"name": "a.jpg", "mime": "image/jpeg", "url": "https://cdn/a.jpg"}}}


def project_raw() -> dict:
    return {
        "url": "https://darglobal.co.uk/trump-tower-jeddah",
        "captured_at": "2026-05-31",
        "pageProps": {
            "globalData": {"attributes": {"DesktopMenu": [{"title": "Menu yang tidak boleh ikut"}]}},
            "projectDetailsData": {
                "attributes": {
                    "title": "Trump Tower Jeddah",
                    "disclaimer": "Not owned by The Trump Organization.",
                    "faqContent": [],
                    "ProjectDetails": {
                        "ProjectBanner": {"Heading": "Trump Tower Jeddah", "Location": "Jeddah, Saudi Arabia", "HeroBanner": MEDIA},
                        "AboutProject": {
                            "description": "<p>Seafront tower on the <b>Jeddah Corniche</b>.</p>",
                            "AboutDetailsType": [
                                {"id": 1, "name": "Property Type", "value": "Seafront Residences"},
                                {"id": 2, "name": "Completion Date", "value": "December 2029"},
                                {"id": 3, "name": "Area", "value": "70 to 365 sqm"},
                            ],
                        },
                        "Amenities": {"amenitiesList": [{"name": "Infinity Pool", "value": "pool"}]},
                        "PhotoGallery": {"galleryList": [{"name": "Master Bedroom", "Image": MEDIA}]},
                        "loaction": {"locationDetailsWithImages": {"lat": "21.5", "long": "39.1", "location": "Jeddah"}},
                        "downloadbrochure": {"file": {"data": {"attributes": {"url": "https://cdn/b.pdf", "mime": "application/pdf"}}}},
                    },
                }
            },
        },
    }


# --- common / darglobal ---


def test_extract_next_data():
    html = '<html><script id="__NEXT_DATA__" type="application/json">{"props": {"pageProps": {"a": 1}}}</script></html>'
    assert extract_next_data(html)["props"]["pageProps"] == {"a": 1}
    assert extract_next_data("<html>challenge</html>") is None


def test_parse_sitemap():
    xml = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://x/a-1</loc><lastmod>2026-11-16</lastmod></url><url><loc>https://x/b</loc></url></urlset>"
    )
    assert parse_sitemap(xml) == [("https://x/a-1", "2026-11-16"), ("https://x/b", None)]


def test_url_key_normalizes():
    assert url_key("https://darglobal.co.uk/Sunrise-Haven/") == "sunrise-haven"
    assert url_key("https://darglobal.co.uk") == ""
    assert key_to_filename("blog/x") == "blog__x.json"
    assert key_to_filename("") == "home.json"


def test_iter_texts_skips_media_menu_and_slugs():
    texts = list(iter_texts(project_raw()["pageProps"]["projectDetailsData"]))
    joined = "\n".join(texts)
    assert "Property Type: Seafront Residences" in joined
    assert "Infinity Pool" in joined and "pool" not in texts
    assert "https://cdn" not in joined
    assert is_media(MEDIA)
    assert list(iter_texts({"a": "Lorem ipsum dolor sit amet", "b": "Real text"})) == ["Real text"]


def test_page_to_project_structure():
    proj = page_to_project(project_raw(), "trump-tower-jeddah", {})
    assert proj is not None
    assert proj.title == "Trump Tower Jeddah"
    assert proj.country == "Saudi Arabia"
    assert proj.completion == "December 2029"
    assert proj.lat == 21.5 and proj.lon == 39.1
    assert proj.brochure_url == "https://cdn/b.pdf"
    assert proj.amenities == ["Infinity Pool"]
    assert proj.source == "DarGlobal"


def test_page_to_chunks_has_header_and_no_gallery_captions():
    raw = project_raw()
    chunks = page_to_chunks(raw, "project", "Trump Tower Jeddah")
    assert chunks, "harus ada chunk"
    assert all(c.text.startswith("# Trump Tower Jeddah — ") for c in chunks)
    assert all(c.source == "DarGlobal" and c.url == raw["url"] for c in chunks)
    assert not any("Master Bedroom" in c.text for c in chunks)
    assert not any("Menu yang tidak boleh ikut" in c.text for c in chunks)
    assert len({c.chunk_id for c in chunks}) == len(chunks)


def test_classify_and_title():
    assert classify("blog/a", {}) == "blog"
    assert classify("blog", {"initialBlogs": []}) == "index"
    assert classify("trump-tower-jeddah", project_raw()["pageProps"]) == "project"
    assert classify("about", {}) == "general"
    assert page_title(project_raw()["pageProps"], "x") == "Trump Tower Jeddah"
    props = {"pageData": {"attributes": {"SEO": {"metaTitle": "About Us | DarGlobal"}}}}
    assert page_title(props, "x") == "About Us"


def test_split_chunks_respects_limit():
    text = "\n".join(f"Line {i} " + "word " * 30 for i in range(40))
    chunks = split_chunks(text, limit=500)
    assert len(chunks) > 1
    assert all(len(c) <= 500 for c in chunks)


def test_merge_short_joins_small_pieces_into_neighbors():
    long_a, long_b = "A" * 300, "B" * 300
    merged = merge_short([("Hero", "Buy now"), ("About", long_a), ("Location", long_b), ("Footer", "Call us")])
    assert [label for label, _ in merged] == ["Hero + About", "Location + Footer"]
    assert merged[0][1].startswith("Buy now") and merged[1][1].endswith("Call us")
    assert all(len(text) >= 150 for _, text in merged)
    assert merge_short([("Only", "short page")]) == [("Only", "short page")]
    assert merge_short([]) == []


def test_page_to_chunks_has_no_tiny_chunks():
    raw = project_raw()
    chunks = page_to_chunks(raw, "project", "Trump Tower Jeddah")
    bodies = [c.text.split("\n\n", 1)[1] for c in chunks]
    assert len(chunks) == 1 or all(len(b) >= 150 for b in bodies)
    assert "Seafront tower" in "\n".join(bodies)


def test_old_text_clean_removes_screen_noise():
    raw = "Property TypeVilla\nProperty Type\nVilla\n01 Golden Visa\n01\nGolden Visa\nOman\nRegister Your Interest\nform"
    cleaned = old_text_clean(raw, {"Oman"})
    assert cleaned.splitlines() == ["Property Type: Villa", "Golden Visa"]
    assert dedupe_lines(["a line\nA line", "b"]) == "a line\nb"


# --- wasalt ---


def wasalt_row(**kw) -> dict:
    row = {
        "id": 1, "purpose": "sale", "title": "Villa 300 SQM", "type": "Villa", "main_type": "Residential",
        "city": "Aldammam", "district": "Al Faisaliyah", "address": "Al Faisaliyah, Dammam", "price": 1_500_000,
        "currency": "SAR", "rent_freq": None, "bedrooms": 5, "bathrooms": None, "area_sqm": "300", "age": "New",
        "furnishing": None, "amenities": [], "lat": "26.4", "lon": "50.1", "published": "2026-11-01T00:00:00Z",
        "url": "https://wasalt.sa/en/property/sale/villa-1",
    }
    row.update(kw)
    return row


def test_clean_row_types_and_normalization():
    listing, reason = clean_row(wasalt_row())
    assert reason is None and listing is not None
    assert listing.city == "Dammam"
    assert listing.area_sqm == 300.0 and listing.price_per_sqm == 5000.0
    assert listing.age_years == 0
    assert listing.bedrooms == 5
    assert "is_active" not in listing.model_dump()
    assert listing.source == "Wasalt"


def test_clean_row_clears_implausible_bedrooms_but_keeps_listing():
    apt, _ = clean_row(wasalt_row(type="Apartment", bedrooms=40, area_sqm="644"))
    tiny, _ = clean_row(wasalt_row(bedrooms=16, area_sqm="144"))  # 9 m² per kamar
    big, _ = clean_row(wasalt_row(bedrooms=20, area_sqm="1673"))  # villa besar, wajar
    no_area, _ = clean_row(wasalt_row(bedrooms=6, area_sqm=None))
    assert apt is not None and apt.bedrooms is None
    assert tiny is not None and tiny.bedrooms is None
    assert big.bedrooms == 20 and no_area.bedrooms == 6
    assert sane_bedrooms(None, "Villa", 300.0) is None
    assert sane_bedrooms(0, "Land", 500.0) == 0


def test_clean_row_rent_monthly_is_annualized():
    rf = {"monthly": {"amount": 4000}, "yearly": {"amount": 4000}}
    listing, _ = clean_row(wasalt_row(purpose="rent", price=4000, rent_freq=rf))
    assert listing.rent_period == "monthly"
    assert listing.rent_yearly_sar == 48_000


def test_clean_row_rejects_bad_prices():
    assert clean_row(wasalt_row(price=0))[0] is None
    assert clean_row(wasalt_row(price=1_031_250_000))[0] is None
    rf = {"yearly": {"amount": 1_427_968_000}}
    assert clean_row(wasalt_row(purpose="rent", price=1_427_968_000, rent_freq=rf))[0] is None


def test_clean_all_dedupes_and_flags_outliers():
    rows = [wasalt_row(id=i, title=f"Villa {i}", price=1_000_000 + i * 10_000) for i in range(1, 12)]
    rows.append(wasalt_row(id=99, title="Villa 1", price=1_010_000))  # isi sama dengan id=1 → duplikat
    rows.append(wasalt_row(id=50, title="Villa 50", price=190_000_000))  # harga ekstrem
    kept, rejected = clean_all(rows)
    reasons = {r["id"]: r["reason"] for r in rejected}
    assert reasons[99] == "duplikat"
    assert "ekstrem" in reasons[50]
    assert {k.id for k in kept} == set(range(1, 12))


def test_helpers():
    assert norm_city("Makkah Al Mukarramah") == "Makkah"
    assert norm_city("Riyadh") == "Riyadh"
    assert parse_age("New") == 0 and parse_age(11) == 11 and parse_age(None) is None


def test_read_jsonl_keeps_unicode_line_separator(tmp_path):
    from dataprep.build import write_jsonl
    from dataprep.load_db import read_jsonl

    path = tmp_path / "x.jsonl"
    write_jsonl(path, [{"text": "a b"}, {"text": "c"}])
    assert read_jsonl(path) == [{"text": "a b"}, {"text": "c"}]


def test_norm_country_and_text_details():
    from dataprep.clean_darglobal import (
        details_from_text,
        location_from_text,
        norm_country,
    )

    assert norm_country("saudi-arabia") == "Saudi Arabia"
    assert norm_country("Dubai | UAE") == "United Arab Emirates"
    assert norm_country("Muscat, Oman - AIDA") == "Oman"
    assert norm_country("AIDA, Muscat") == "Oman"
    assert norm_country(None) is None
    text = "Gallery\nDW1\nDubai | UAE\nStatus: Under Development\nAREA (SQFT): 764 - 7,720"
    assert location_from_text(text) == "Dubai, UAE"
    assert details_from_text(text) == {"Status": "Under Development", "Area (Sqft)": "764 - 7,720"}


def test_dedupe_chunks_prefers_project_page():
    from dataprep.build import dedupe_chunks
    from dataprep.clean_darglobal import DocChunk

    def mk(cid, kind, title):
        return DocChunk(chunk_id=cid, url=f"u/{cid}", kind=kind, title=title, section="S",
                        text=f"# {title} — S\n\nSame body", captured_at="2026-05-31")

    out = dedupe_chunks([mk("a", "general", "Listing"), mk("b", "project", "DG1"), mk("c", "blog", "Post"), mk("d", "project", "W")])
    assert [c.chunk_id for c in out] == ["b", "d"]
    assert classify("projects/properties-in-uae", {}) == "index"
