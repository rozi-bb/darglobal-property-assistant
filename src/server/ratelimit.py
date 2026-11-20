"""Rate limit sederhana (di memori) untuk membatasi biaya LLM.

- Per IP: maks N pesan per jendela waktu (default 20 pesan / 10 menit).
- Global: maks M pesan per 24 jam untuk semua user (default 1000), pagar biaya kalau URL disebar.
Dipakai bersama oleh `/api/chat` dan UI Chainlit. Hitungan hilang saat app restart dan hanya berlaku
per proses (cukup untuk satu container).

Env:
- `RATE_LIMIT_PER_IP`        : "jumlah/detik", mis. "20/600"; "0" = mati
- `RATE_LIMIT_GLOBAL_PER_DAY`: angka; "0" = mati
- `TRUST_PROXY_HEADERS`      : "true" kalau app di belakang proxy/tunnel (IP dibaca dari `CF-Connecting-IP` /
  `X-Forwarded-For`). Jangan diaktifkan tanpa proxy: header itu bisa dipalsukan client.
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache

DEFAULT_PER_IP = "20/600"
DEFAULT_GLOBAL_PER_DAY = 1000
DAY_S = 86_400.0
GLOBAL_KEY = "__global__"


@dataclass(frozen=True)
class Limit:
    """Maks `count` pesan per `window_s` detik."""

    count: int
    window_s: float


def parse_limit(value: str | None, default: str = DEFAULT_PER_IP) -> Limit | None:
    """'20/600' → Limit(20, 600). '0' / kosong-ke-default. None = rate limit mati."""
    raw = (value if value is not None and value.strip() else default).strip()
    if raw == "0":
        return None
    count, _, window = raw.partition("/")
    limit = Limit(int(count), float(window or 60))
    if limit.count <= 0 or limit.window_s <= 0:
        raise ValueError(f"Format rate limit salah: {raw!r} (contoh: 20/600)")
    return limit


@dataclass(frozen=True)
class Decision:
    """Hasil cek: boleh lanjut, atau tunggu `retry_after_s` detik."""

    allowed: bool
    retry_after_s: int = 0
    scope: str = ""  # "ip" / "global" kalau ditolak


class RateLimiter:
    """Sliding window per kunci (IP) + satu kuota global harian."""

    def __init__(
        self, per_ip: Limit | None, global_per_day: int | None, clock: Callable[[], float] = time.monotonic
    ) -> None:
        """`clock` bisa diganti di test."""
        self.per_ip = per_ip
        self.global_limit = Limit(global_per_day, DAY_S) if global_per_day else None
        self.clock = clock
        self.hits: dict[str, deque[float]] = {}
        self.lock = threading.Lock()

    def _wait(self, key: str, limit: Limit, now: float) -> float:
        """Detik sampai `key` boleh lagi (0 = boleh). Buang hit yang sudah di luar jendela."""
        q = self.hits.get(key)
        if q is None:
            return 0.0
        while q and q[0] <= now - limit.window_s:
            q.popleft()
        if not q:
            del self.hits[key]
            return 0.0
        return q[0] + limit.window_s - now if len(q) >= limit.count else 0.0

    def check(self, ip: str) -> Decision:
        """Catat satu pesan dari `ip` kalau masih dalam batas."""
        now = self.clock()
        with self.lock:
            checks = [("global", GLOBAL_KEY, self.global_limit), ("ip", f"ip:{ip}", self.per_ip)]
            for scope, key, limit in checks:
                if limit is not None and (wait := self._wait(key, limit, now)) > 0:
                    return Decision(False, max(1, math.ceil(wait)), scope)
            for _, key, limit in checks:
                if limit is not None:
                    self.hits.setdefault(key, deque()).append(now)
        return Decision(True)


def trust_proxy_headers() -> bool:
    """`TRUST_PROXY_HEADERS=true` → IP client dibaca dari header proxy."""
    return os.environ.get("TRUST_PROXY_HEADERS", "").strip().lower() in {"1", "true", "yes"}


def client_ip(headers: Mapping[str, str], peer: str | None, trust_proxy: bool) -> str:
    """IP client. Header proxy hanya dipercaya kalau app memang di belakang proxy."""
    if trust_proxy:
        lowered = {k.lower(): v for k, v in headers.items()}
        if ip := lowered.get("cf-connecting-ip", "").strip():
            return ip
        if forwarded := lowered.get("x-forwarded-for", "").strip():
            return forwarded.split(",")[0].strip()
    return peer or "unknown"


def environ_headers(environ: Mapping[str, object]) -> dict[str, str]:
    """Header HTTP dari environ WSGI (koneksi socket Chainlit): HTTP_X_FORWARDED_FOR → x-forwarded-for."""
    return {k[5:].replace("_", "-").lower(): str(v) for k, v in environ.items() if k.startswith("HTTP_")}


def limit_message(decision: Decision) -> str:
    """Pesan ramah untuk user yang kena batas."""
    minutes = max(1, math.ceil(decision.retry_after_s / 60))
    if decision.scope == "global":
        return ("The assistant has reached its daily usage limit. "
                f"Please try again in about {minutes} minute{'s' if minutes > 1 else ''}.")
    return (f"You're sending messages too quickly. Please try again in about {minutes} "
            f"minute{'s' if minutes > 1 else ''}.")


@lru_cache(maxsize=1)
def get_limiter() -> RateLimiter:
    """Limiter bersama (API + UI), dibuat dari env saat pertama dipakai."""
    global_raw = os.environ.get("RATE_LIMIT_GLOBAL_PER_DAY", "").strip()
    global_per_day = int(global_raw) if global_raw else DEFAULT_GLOBAL_PER_DAY
    return RateLimiter(parse_limit(os.environ.get("RATE_LIMIT_PER_IP")), global_per_day or None)
