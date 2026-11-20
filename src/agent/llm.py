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
        max_retries=0,  # retry + backoff diurus ModelRetryMiddleware
    )


@lru_cache(maxsize=1)
def get_fallback_llm() -> BaseChatModel | None:
    """Model cadangan (misal OpenRouter) kalau `FALLBACK_LLM_*` diisi semua; selain itu None."""
    api_key = os.environ.get("FALLBACK_LLM_API_KEY", "").strip()
    base_url = os.environ.get("FALLBACK_LLM_BASE_URL", "").strip()
    model = os.environ.get("FALLBACK_LLM_MODEL", "").strip()
    if not (api_key and base_url and model):
        return None
    return ChatOpenAI(model=model, base_url=base_url, api_key=api_key, temperature=0.2, timeout=60, max_retries=0)
