"""Utilitas bersama pipeline data."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"

# UA generik, tanpa data pribadi. Tidak menyamar sebagai browser.
USER_AGENT = "darglobal-chatbot-dataprep/0.1 (non-commercial research)"

_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def setup_logging() -> None:
    """Pasang format log sederhana untuk script CLI."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def extract_next_data(html: str) -> dict[str, Any] | None:
    """Ambil JSON `__NEXT_DATA__` (data CMS bawaan halaman Next.js); None kalau tidak ada."""
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def parse_sitemap(xml_text: str) -> list[tuple[str, str | None]]:
    """Kembalikan daftar (loc, lastmod) dari isi sitemap XML."""
    root = ElementTree.fromstring(xml_text)
    out: list[tuple[str, str | None]] = []
    for url in root.iter(f"{_SITEMAP_NS}url"):
        loc = url.findtext(f"{_SITEMAP_NS}loc")
        if loc:
            out.append((loc.strip(), url.findtext(f"{_SITEMAP_NS}lastmod")))
    return out


def write_json(path: Path, data: Any) -> None:
    """Tulis JSON UTF-8 rapi; buat folder induk kalau belum ada."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
