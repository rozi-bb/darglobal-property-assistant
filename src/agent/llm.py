"""Koneksi ke LLM lewat API yang kompatibel OpenAI.

Default: DeepSeek API langsung. Bisa diganti ke provider lain (misal OpenRouter)
cukup lewat env `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`.

API key hanya dibaca dari environment variable, tidak pernah ditulis di kode.
"""

from __future__ import annotations

import os
from functools import lru_cache

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"


@lru_cache(maxsize=1)
def get_llm() -> BaseChatModel:
    """Return chat model (dibuat sekali, lalu dipakai ulang)."""
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        raise RuntimeError("LLM_API_KEY belum diisi. Salin .env.example ke .env lalu isi key-nya.")

    return ChatOpenAI(
        model=os.environ.get("LLM_MODEL") or DEFAULT_MODEL,
        base_url=os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL,
        api_key=api_key,
        temperature=0.2,
        timeout=60,
        max_retries=2,
    )
