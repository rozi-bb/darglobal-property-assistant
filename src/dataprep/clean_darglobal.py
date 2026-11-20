"""Ubah JSON CMS DarGlobal (Strapi via `__NEXT_DATA__`) jadi data proyek terstruktur + potongan dokumen RAG."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from markdownify import markdownify
from pydantic import BaseModel, Field

SOURCE = "DarGlobal"

# Kunci yang bukan konten (metadata CMS, tombol, menu global, duplikat HTML).
SKIP_KEYS = {
    "globalData", "isMobile", "locale", "socialLinks", "blogDetailsHTML", "initialBlogs", "initialMeta",
    "localizations", "versions", "id", "createdAt", "updatedAt", "publishedAt", "vuid", "versionNumber",
    "versionComment", "isVisibleInListView", "slug", "href", "target", "events", "ButtonTheme", "label",
    "canonicalURL", "metaRobots", "metaViewport", "keywords", "structuredData", "metaSocial", "metaImage",
    "SEO", "LiveFeed", "View360", "bookunit", "chooseYourResidence", "viewall", "viewAll", "exploreButton",
    "button", "CTA", "icon", "lat", "long", "projectCode", "includeInviteForm", "provider", "url",
    "galleryList", "newGallery", "ProgressUpdate", "OtherProject", "formbgColor", "AmenityImg",
}
_URLISH = re.compile(r"^(https?://|/|#|mailto:|tel:)")
_SLUGISH = re.compile(r"^[a-z0-9]+([-_][a-z0-9]+)*$")
_HTML = re.compile(r"<[a-zA-Z/][^>]*>")
_WS = re.compile(r"[ \t ]+")
_MULTI_NL = re.compile(r"\n{3,}")
CHUNK_CHARS = 1200
MIN_CHUNK_CHARS = 150  # potongan lebih pendek digabung ke potongan tetangga di halaman yang sama


class Project(BaseModel):
    """Satu proyek DarGlobal (data terstruktur untuk tabel `darglobal_projects`)."""

    slug: str
    url: str
    title: str
    location: str | None = None
    country: str | None = None
    starting_price: str | None = None
    property_type: str | None = None
    status: str | None = None
    units: str | None = None
    completion: str | None = None
    area: str | None = None
    description: str | None = None
    details: dict[str, str] = Field(default_factory=dict)
    amenities: list[str] = Field(default_factory=list)
    key_features: list[str] = Field(default_factory=list)
    lat: float | None = None
    lon: float | None = None
    brochure_url: str | None = None
    image_url: str | None = None
    disclaimer: str | None = None
    captured_at: str
    source: str = SOURCE


class DocChunk(BaseModel):
    """Satu potongan dokumen untuk index RAG (tabel `darglobal_chunks`)."""

    chunk_id: str
    url: str
    kind: str
    title: str
    section: str
    text: str
    captured_at: str
    source: str = SOURCE


def clean_text(value: str) -> str:
    """HTML → markdown, rapikan spasi; teks biasa tetap."""
    text = markdownify(value, heading_style="ATX", strip=["img", "span", "u"]) if _HTML.search(value) else value
    text = text.replace("\\-", "-").replace("\r", "")
    lines = [_WS.sub(" ", ln).strip() for ln in text.split("\n")]
    return _MULTI_NL.sub("\n\n", "\n".join(lines)).strip()


def is_media(obj: Any) -> bool:
    """Cek apakah objek adalah media Strapi (gambar/video/file), bukan konten teks."""
    if not isinstance(obj, dict):
        return False
    if "mime" in obj or "formats" in obj:
        return True
    data = obj.get("data")
    if isinstance(data, list) and data:
        data = data[0]
    if isinstance(data, dict):
        attrs = data.get("attributes", {})
        return isinstance(attrs, dict) and ("mime" in attrs or "formats" in attrs)
    return set(obj) == {"data"} and data is None


def keep_string(value: str) -> bool:
    """Saring string yang bukan konten: URL, slug/nilai config, nama file, simbol saja."""
    v = value.strip()
    if len(v) < 2 or not re.search(r"[A-Za-z0-9؀-ۿ]", v):
        return False
    if _URLISH.match(v) or _SLUGISH.match(v) or "lorem ipsum" in v.lower():
        return False  # URL, slug/nilai config, atau teks placeholder CMS
    return not re.search(r"\.(jpe?g|png|webp|svg|pdf|mp4|webm)$", v, re.I)


def iter_texts(obj: Any) -> Iterator[str]:
    """Telusuri JSON dan hasilkan potongan teks konten secara berurutan."""
    if isinstance(obj, str):
        if keep_string(obj):
            yield clean_text(obj)
    elif isinstance(obj, list):
        for item in obj:
            yield from iter_texts(item)
    elif isinstance(obj, dict) and not is_media(obj):
        # Pasangan name/value (mis. "Property Type: Seafront Residences").
        if isinstance(obj.get("name"), str) and isinstance(obj.get("value"), str):
            name = clean_text(obj["name"]).rstrip(":").strip()
            # Nilai berupa slug ikon (mis. "golf-cart") → cukup namanya.
            yield f"{name}: {clean_text(obj['value'])}" if keep_string(obj["value"]) else name
            return
        # Statistik: {"label": "$23B", "value": "GROSS DEVELOPMENT VALUE"} → "GROSS DEVELOPMENT VALUE: $23B".
        if isinstance(obj.get("label"), str) and isinstance(obj.get("value"), str) and keep_string(obj["value"]):
            yield f"{clean_text(obj['value'])}: {clean_text(obj['label'])}"
            return
        for key, value in obj.items():
            if key in SKIP_KEYS or key == "name" and "value" in obj:
                continue
            yield from iter_texts(value)


def dedupe_lines(parts: list[str]) -> str:
    """Gabung potongan teks, buang baris yang sama persis (CMS sering mengulang)."""
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        for line in part.split("\n"):
            key = line.strip().lower()
            if key and key in seen and len(key) > 3:
                continue
            seen.add(key)
            out.append(line)
    return _MULTI_NL.sub("\n\n", "\n".join(out)).strip()


# Halaman pemasaran/indeks yang memakai template proyek tapi bukan satu proyek.
NOT_PROJECTS = {"one-of-one", "landing-page", "invest-crypto", "invest-in-aida"}
# Awalan path → negara (sub-proyek Saudi/Oman yang lokasinya tidak tertulis di halaman).
PATH_COUNTRY = {"rayana": "Saudi Arabia", "amaya": "Saudi Arabia", "trump-plaza": "Saudi Arabia", "play-project": "Oman"}


def classify(path_key: str, page_props: dict[str, Any]) -> str:
    """Jenis dokumen dari URL dan isi: project/blog/press/insight/index/general."""
    first = path_key.split("/")[0]
    if path_key in NOT_PROJECTS:
        return "general"
    if "initialBlogs" in page_props or path_key in {"blog", "press-release", "insights", "media"} or first == "projects":
        return "index"  # halaman daftar/filter: isinya ringkasan halaman lain
    if first == "blog":
        return "blog"
    if first in {"press-release", "press", "media", "news"} or "media" in first:
        return "press"
    if first in {"insights", "insight"}:
        return "insight"
    if "projectDetailsData" in page_props or first in {"one-of-one", "amaya", "rayana", "trump-plaza", "play-project", "lp", "landing-page"}:
        return "project"
    return "general"


def page_title(page_props: dict[str, Any], fallback: str, path_key: str = "") -> str:
    """Judul halaman: heading proyek/blog, lalu title/meta title SEO konten utama, lalu fallback."""
    pdd = (page_props.get("projectDetailsData") or {}).get("attributes") or {}
    banner = (pdd.get("ProjectDetails") or {}).get("ProjectBanner") or {}
    blog = ((page_props.get("blogDetails") or {}).get("data") or {}).get("attributes") or {}
    for cand in (banner.get("Heading"), pdd.get("title"), pdd.get("projectTitle"), blog.get("heading"), blog.get("title")):
        if isinstance(cand, str) and cand.strip():
            return clean_text(cand)
    holders = [node for _, node in main_holders(page_props, path_key) if isinstance(node, dict)]
    for node in holders:
        if isinstance(node.get("title"), str) and node["title"].strip():
            return clean_text(node["title"])
    for node in holders:
        seo = node.get("SEO")
        if isinstance(seo, dict) and seo.get("metaTitle"):
            return clean_text(re.split(r" [-|] DarGlobal| \| ", str(seo["metaTitle"]))[0])
    return fallback


def unwrap(value: Any) -> Any:
    """Buka bungkus Strapi: {data: {attributes: {...}}} / {attributes: {...}} / {data: [x]} → isi."""
    node = value
    for _ in range(3):
        if isinstance(node, dict) and node and set(node) <= {"data", "meta"}:
            node = node.get("data")
        if isinstance(node, list) and len(node) == 1:
            node = node[0]
        if isinstance(node, dict) and isinstance(node.get("attributes"), dict):
            node = node["attributes"]
    return node


def main_holders(page_props: dict[str, Any], path_key: str = "") -> list[tuple[str, Any]]:
    """Objek konten utama halaman (sudah di-unwrap), tanpa data global/komponen bersama yang salah tempat."""
    out: list[tuple[str, Any]] = []
    slug = str(page_props.get("slug") or "").lower()
    if slug and isinstance(page_props.get("collectionsData"), list):
        # /one-of-one/[slug]: tiap halaman memuat SEMUA koleksi + intro bersama → ambil item miliknya saja.
        for item in page_props["collectionsData"]:
            attrs = unwrap(item)
            if isinstance(attrs, dict) and str(attrs.get("slug", "")).lower() == slug:
                return [("collection", attrs)]
    for key, value in page_props.items():
        if key in SKIP_KEYS:
            continue
        node = unwrap(value)
        title = str(node.get("title", "")).strip().lower() if isinstance(node, dict) else ""
        if title == "privacy policy" and "privacy" not in path_key:
            continue  # halaman /about ikut memuat data kebijakan privasi
        out.append((key, node))
    return out


def content_sections(page_props: dict[str, Any], path_key: str = "") -> list[tuple[str, Any]]:
    """Bagi isi halaman jadi (nama seksi, objek JSON) sesuai komponen CMS tingkat atas."""
    pdd = (page_props.get("projectDetailsData") or {}).get("attributes")
    if isinstance(pdd, dict):
        sections = [(k, v) for k, v in (pdd.get("ProjectDetails") or {}).items() if k not in SKIP_KEYS]
        sections += [(k, pdd.get(k)) for k in ("faqContent", "backyardDetails", "newAmenities", "disclaimer")]
        return sections
    out: list[tuple[str, Any]] = []
    for key, node in main_holders(page_props, path_key):
        if isinstance(node, dict):
            out += [(k, v) for k, v in node.items() if k not in SKIP_KEYS]
        else:
            out.append((key, node))
    return out


def humanize(key: str) -> str:
    """'whyInvest' → 'Why Invest', 'faqContent' → 'Faq Content'."""
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", key).replace("_", " ")
    return {"loaction": "Location"}.get(key, spaced.strip().title())


def split_chunks(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """Pecah teks per paragraf/baris jadi potongan ≤ limit karakter."""
    blocks = [b for b in re.split(r"\n\s*\n|\n(?=#)", text) if b.strip()]
    chunks: list[str] = []
    cur = ""
    for block in blocks:
        while len(block) > limit:  # blok sangat panjang: potong di akhir baris/kalimat
            cut = max(block.rfind("\n", 0, limit), block.rfind(". ", 0, limit) + 1)
            cut = cut if cut > limit // 3 else limit
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(block[:cut].strip())
            block = block[cut:].strip()
        if cur and len(cur) + len(block) + 2 > limit:
            chunks.append(cur)
            cur = block
        else:
            cur = f"{cur}\n\n{block}" if cur else block
    if cur.strip():
        chunks.append(cur)
    return chunks


def _join_label(a: str, b: str) -> str:
    return a if a == b else f"{a} + {b}"


def merge_short(pieces: list[tuple[str, str]], min_chars: int = MIN_CHUNK_CHARS) -> list[tuple[str, str]]:
    """Gabung potongan (seksi, teks) yang lebih pendek dari `min_chars` ke potongan berikutnya.

    Potongan pendek di akhir halaman digabung ke potongan sebelumnya. Halaman yang seluruhnya pendek
    tetap jadi satu potongan. Hasil bisa sedikit melewati CHUNK_CHARS (masih jauh di bawah batas bge-small).
    """
    out: list[tuple[str, str]] = []
    carry: tuple[str, str] | None = None
    for label, text in pieces:
        if carry is not None:
            label, text = _join_label(carry[0], label), f"{carry[1]}\n\n{text}"
            carry = None
        if len(text) < min_chars:
            carry = (label, text)
        else:
            out.append((label, text))
    if carry is not None:
        if out:
            prev_label, prev_text = out[-1]
            out[-1] = (_join_label(prev_label, carry[0]), f"{prev_text}\n\n{carry[1]}")
        else:
            out.append(carry)
    return out


def page_to_chunks(raw: dict[str, Any], kind: str, title: str, path_key: str = "") -> list[DocChunk]:
    """Satu halaman mentah → potongan dokumen per seksi (dengan header judul untuk konteks)."""
    pieces: list[tuple[str, str]] = []
    for section_key, obj in content_sections(raw["pageProps"], path_key):
        text = dedupe_lines(list(iter_texts(obj)))
        if len(text) < 40:
            continue
        pieces += [(humanize(section_key), piece) for piece in split_chunks(text)]
    out: list[DocChunk] = []
    for i, (section, piece) in enumerate(merge_short(pieces)):
        cid = hashlib.sha1(f"{raw['url']}|{section}|{i}".encode()).hexdigest()[:16]
        out.append(DocChunk(
            chunk_id=cid, url=raw["url"], kind=kind, title=title, section=section,
            text=f"# {title} — {section}\n\n{piece}", captured_at=raw["captured_at"],
        ))
    return out


def _media_url(obj: Any) -> str | None:
    data = (obj or {}).get("data") if isinstance(obj, dict) else None
    if isinstance(data, list):
        data = data[0] if data else None
    attrs = (data or {}).get("attributes") or {}
    return attrs.get("url")


def _to_float(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _pick(details: dict[str, str], *needles: str) -> str | None:
    for name, value in details.items():
        if any(n in name.lower() for n in needles):
            return value
    return None


def page_to_project(raw: dict[str, Any], slug: str, country_by_name: dict[str, str]) -> Project | None:
    """Halaman detail proyek (`projectDetailsData`) → record Project; None kalau bukan template itu."""
    pdd = (raw["pageProps"].get("projectDetailsData") or {}).get("attributes")
    if not isinstance(pdd, dict):
        return None
    pd = pdd.get("ProjectDetails") or {}
    banner = pd.get("ProjectBanner") or {}
    about = pd.get("AboutProject") or {}
    loc = ((pd.get("loaction") or {}).get("locationDetailsWithImages")) or {}
    details = {
        clean_text(d["name"]).rstrip(":"): clean_text(d["value"])
        for d in about.get("AboutDetailsType") or []
        if isinstance(d, dict) and d.get("name") and d.get("value")
    }
    title = clean_text(banner.get("Heading") or pdd.get("title") or pdd.get("projectTitle") or slug)
    location = clean_text(banner.get("Location") or loc.get("location") or "") or None
    country = norm_country(country_by_name.get(title.lower()) or location or PATH_COUNTRY.get(slug.split("/")[0]))
    return Project(
        slug=slug,
        url=raw["url"],
        title=title,
        location=location,
        country=country,
        starting_price=clean_text(str(banner["StartingPrice"])) if banner.get("StartingPrice") else None,
        property_type=_pick(details, "property type", "type"),
        status=_pick(details, "status"),
        units=_pick(details, "unit"),
        completion=_pick(details, "completion", "handover"),
        area=_pick(details, "area", "size"),
        description=clean_text(about.get("richDescription") or about.get("description") or "") or None,
        details=details,
        amenities=[clean_text(a["name"]) for a in (pd.get("Amenities") or {}).get("amenitiesList") or [] if a.get("name")],
        key_features=[
            clean_text(k.get("Title") or k.get("title") or "")
            for k in (pd.get("KeyFeatures") or {}).get("keyfeaturelist") or []
            if (k.get("Title") or k.get("title"))
        ],
        lat=_to_float(loc.get("lat")),
        lon=_to_float(loc.get("long")),
        brochure_url=_media_url((pd.get("downloadbrochure") or {}).get("file")),
        image_url=_media_url(banner.get("HeroBanner")) or _media_url(banner.get("desktopThumbnail")),
        disclaimer=clean_text(pdd.get("disclaimer") or "") or None,
        captured_at=raw["captured_at"],
    )


_COUNTRY_ALIASES = {
    "uae": "United Arab Emirates", "ksa": "Saudi Arabia", "uk": "United Kingdom",
    # kota/area → negara (halaman sering hanya menulis kota)
    "dubai": "United Arab Emirates", "rak": "United Arab Emirates", "abu dhabi": "United Arab Emirates",
    "muscat": "Oman", "aida": "Oman", "riyadh": "Saudi Arabia", "jeddah": "Saudi Arabia",
    "doha": "Qatar", "london": "United Kingdom", "noonu atoll": "Maldives", "marbella": "Spain",
    "benahavís": "Spain", "benahavis": "Spain",
}


def norm_country(value: str | None) -> str | None:
    """Seragamkan nama negara: 'saudi-arabia' → 'Saudi Arabia', 'UAE' → 'United Arab Emirates'."""
    if not value:
        return None
    v = value.split("|")[-1].split(",")[-1].split(" - ")[0].strip().replace("-", " ")
    if not v:
        return None
    return _COUNTRY_ALIASES.get(v.lower(), v.title() if v.islower() else v)


_DETAIL_LINE = re.compile(r"^([A-Za-z][A-Za-z ()/&]{2,40}): (.{1,120})$")


def details_from_text(text: str) -> dict[str, str]:
    """Ambil pasangan 'Label: Nilai' pendek dari teks (untuk halaman fallback tanpa JSON)."""
    out: dict[str, str] = {}
    for line in text.split("\n"):
        m = _DETAIL_LINE.match(line.strip())
        if m and m.group(1).strip().title() not in out:
            out[m.group(1).strip().title()] = m.group(2).strip()
    return out


def location_from_text(text: str) -> str | None:
    """Baris lokasi pola 'Kota | Negara' (dari teks layar lama)."""
    for line in text.split("\n")[:12]:
        if re.fullmatch(r"[^|:]{2,40} \| [^|:]{2,40}", line.strip()):
            return line.strip().replace(" | ", ", ")
    return None


def pick_detail(details: dict[str, str], *needles: str) -> str | None:
    """Nilai detail pertama yang namanya mengandung salah satu kata kunci."""
    return _pick(details, *needles)


def country_index(pages: list[dict[str, Any]]) -> dict[str, str]:
    """Nama proyek (huruf kecil) → negara, dari daftar proyek global situs."""
    for raw in pages:
        attrs = (raw["pageProps"].get("globalData") or {}).get("attributes") or {}
        projects = attrs.get("ProjectList") or []
        if projects:
            return {
                str(p.get("name", "")).lower(): norm_country(str(p.get("country", "")).split("|")[0]) or ""
                for p in projects if p.get("name") and p.get("country")
            }
    return {}


def old_text_clean(text: str, countries: set[str]) -> str:
    """Bersihkan teks layar dari scrape lama: buang baris gabungan dobel, daftar negara, boilerplate form."""
    lines = [ln.strip() for ln in text.split("\n")]
    out: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        # Pola "LabelNilai" diikuti "Label" lalu "Nilai" → simpan "Label: Nilai".
        if i + 2 < len(lines) and lines[i + 1] and lines[i + 2] and ln == lines[i + 1] + lines[i + 2]:
            out.append(f"{lines[i + 1]}: {lines[i + 2]}")
            i += 3
            continue
        # Pola "01 Teks" diikuti "01" lalu "Teks" → simpan sekali.
        if i + 2 < len(lines) and ln == f"{lines[i + 1]} {lines[i + 2]}":
            out.append(lines[i + 2])
            i += 3
            continue
        if ln.startswith("Register Your Interest"):
            break
        if ln and ln not in countries:
            out.append(ln)
        i += 1
    return dedupe_lines(out)


def load_raw_pages(raw_dir: Path) -> list[dict[str, Any]]:
    """Baca semua JSON halaman mentah hasil scrape."""
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(raw_dir.glob("*.json"))]
