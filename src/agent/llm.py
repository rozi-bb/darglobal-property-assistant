"""Koneksi ke LLM lewat OpenRouter (API kompatibel OpenAI).

API key hanya dibaca dari environment variable, tidak pernah ditulis di kode.
"""

from __future__ import annotations

import os
from functools import lru_cache

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "google/gemma-4-31b-it:free"


@lru_cache(maxsize=1)
def get_llm() -> BaseChatModel:
    """Return chat model OpenRouter (dibuat sekali, lalu dipakai ulang)."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY belum diisi. Salin .env.example ke .env lalu isi key-nya.")

    return ChatOpenAI(
        model=os.environ.get("OPENROUTER_MODEL") or DEFAULT_MODEL,
        base_url=os.environ.get("OPENROUTER_BASE_URL") or DEFAULT_BASE_URL,
        api_key=api_key,
        temperature=0.2,
        timeout=60,
        max_retries=2,
        # Header opsional OpenRouter untuk menamai aplikasi di dashboard mereka.
        default_headers={"X-Title": "DarGlobal Property Assistant"},
    )
