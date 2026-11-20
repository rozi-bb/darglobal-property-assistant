"""Config backend. Semua nilai (termasuk secret) dibaca dari environment / `.env`."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    """Pengaturan aplikasi."""

    database_url: str
    """Kosong = memori chat di RAM (hanya untuk test/dev cepat)."""

    app_name: str = "DarGlobal Property Assistant"


def get_settings() -> Settings:
    """Baca pengaturan dari environment (`.env` tidak menimpa env yang sudah ada)."""
    load_dotenv(override=False)
    return Settings(database_url=os.environ.get("CHECKPOINT_DATABASE_URL", "").strip())
