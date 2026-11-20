"""Scrape DarGlobal lewat arsip Wayback Machine (situs live dilindungi CAPTCHA, tidak di-bypass).

Alur: daftar URL dari sitemap resmi (+ URL data lama) → CDX Wayback → snapshot asli (`id_`)
terbaru yang bukan halaman challenge → simpan JSON `__NEXT_DATA__.props.pageProps`.

Jalankan: `uv run python -m dataprep.darglobal` (bisa dilanjutkan; file yang sudah ada dilewati).
"""

from __future__ import annotations

import json
import logging
import time
from urllib.parse import urlsplit

import httpx

from dataprep.common import (
    DATA_DIR,
    RAW_DIR,
    USER_AGENT,
    extract_next_data,
    parse_sitemap,
    setup_logging,
    write_json,
)

log = logging.getLogger("darglobal")

OUT_DIR = RAW_DIR / "darglobal" / "pages"
CDX_URL = "https://web.archive.org/cdx/search/cdx"
SNAPSHOT_URL = "https://web.archive.org/web/{ts}id_/{url}"
MIN_INTERVAL_S = 4.5  # jarak minimal antar-request; Wayback ±15 req/menit per IP
MIN_LENGTH = 5000  # halaman challenge Imperva ±1 KB
MAX_TRIES_PER_URL = 3


def url_key(url: str) -> str:
    """Kunci normal sebuah URL DarGlobal: path huruf kecil tanpa slash di ujung ('' = beranda)."""
    path = urlsplit(url if "://" in url else f"https://{url}").path
    return path.strip("/").lower()


def key_to_filename(key: str) -> str:
    """Nama file aman untuk sebuah kunci URL."""
    return (key.replace("/", "__") or "home") + ".json"


_last_request = 0.0


def _throttle() -> None:
    """Tunggu sampai jarak dari request sebelumnya ≥ MIN_INTERVAL_S."""
    global _last_request
    wait = MIN_INTERVAL_S - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def get_with_retry(client: httpx.Client, url: str, params: dict[str, str] | None = None) -> httpx.Response | None:
    """GET (dengan jeda sopan) + backoff saat 429/5xx/error jaringan; None kalau tetap gagal."""
    for attempt in range(4):
        _throttle()
        try:
            resp = client.get(url, params=params)
        except httpx.HTTPError as exc:
            log.warning("error jaringan %s: %s", url, exc)
        else:
            if resp.status_code < 400 or resp.status_code == 404:
                return resp
            log.warning("HTTP %s untuk %s", resp.status_code, url)
        time.sleep(30 * (attempt + 1))
    return None


def load_target_urls() -> dict[str, str]:
    """Kunci → URL: sitemap resmi + URL di data lama yang tidak ada di sitemap."""
    targets: dict[str, str] = {}
    sitemap = (RAW_DIR / "darglobal" / "sitemap.xml").read_text(encoding="utf-8")
    for loc, _ in parse_sitemap(sitemap):
        targets.setdefault(url_key(loc), loc)
    old = json.loads((DATA_DIR / "darglobal_pages.json").read_text(encoding="utf-8"))
    for row in old:
        targets.setdefault(url_key(row["url"]), row["url"])
    return targets


def load_captures(client: httpx.Client) -> dict[str, list[tuple[str, str]]]:
    """Kunci → daftar (timestamp, original) kandidat snapshot, terbaru dulu."""
    cache = RAW_DIR / "darglobal" / "cdx.json"
    if cache.exists():
        rows = json.loads(cache.read_text(encoding="utf-8"))
    else:
        params = {
            "url": "darglobal.co.uk/*",
            "output": "json",
            "filter": "statuscode:200",
            "from": "2025",
            "fl": "original,timestamp,length,mimetype",
        }
        resp = get_with_retry(client, CDX_URL, params)
        if resp is None:
            raise RuntimeError("CDX Wayback gagal diambil")
        rows = resp.json()
        write_json(cache, rows)
    captures: dict[str, list[tuple[str, str]]] = {}
    for original, ts, length, mime in rows[1:]:
        if "html" not in mime or not length.isdigit() or int(length) < MIN_LENGTH:
            continue
        if urlsplit(original).query:
            continue
        captures.setdefault(url_key(original), []).append((ts, original))
    for cands in captures.values():
        cands.sort(reverse=True)
    return captures


def fetch_page(client: httpx.Client, key: str, url: str, cands: list[tuple[str, str]]) -> bool:
    """Coba snapshot terbaru sampai dapat `__NEXT_DATA__`; simpan ke data/raw. True kalau berhasil."""
    for ts, original in cands[:MAX_TRIES_PER_URL]:
        snap = SNAPSHOT_URL.format(ts=ts, url=original)
        resp = get_with_retry(client, snap)
        if resp is None or resp.status_code != 200:
            continue
        nd = extract_next_data(resp.text)
        props = (nd or {}).get("props", {}).get("pageProps")
        if not props:
            log.info("snapshot %s tanpa __NEXT_DATA__, coba yang lebih lama", ts)
            continue
        write_json(
            OUT_DIR / key_to_filename(key),
            {
                "url": url,
                "captured_at": f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}",
                "wayback_url": snap,
                "page": nd.get("page"),
                "pageProps": props,
            },
        )
        return True
    return False


def main() -> None:
    """Jalankan scrape DarGlobal dari arsip Wayback."""
    setup_logging()
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(headers=headers, timeout=60, follow_redirects=True) as client:
        targets = load_target_urls()
        captures = load_captures(client)
        missing: list[str] = []
        done = skipped = 0
        for i, (key, url) in enumerate(sorted(targets.items()), 1):
            if (OUT_DIR / key_to_filename(key)).exists():
                skipped += 1
                continue
            cands = captures.get(key, [])
            if not cands or not fetch_page(client, key, url, cands):
                missing.append(url)
                continue
            done += 1
            log.info("[%d/%d] ok %s", i, len(targets), url)
        write_json(RAW_DIR / "darglobal" / "missing.json", missing)
        log.info("selesai: baru %d, sudah ada %d, tidak ada di arsip %d", done, skipped, len(missing))


if __name__ == "__main__":
    main()
