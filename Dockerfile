# DarGlobal Property Assistant: FastAPI + Chainlit + LangGraph
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.11 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# 1) Dependency dulu (di-cache selama pyproject/uv.lock tidak berubah)
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# 2) Kode, data, dan config UI
COPY src ./src
COPY data ./data
COPY .chainlit/config.toml ./.chainlit/config.toml
COPY chainlit.md ./
COPY public ./public
RUN uv sync --frozen --no-dev

# 3) Model embedding bge-small (±130 MB) di-download saat build, bukan tiap container start
ENV FASTEMBED_CACHE_PATH=/app/.cache/fastembed
RUN python -c "from agent.retrieval import get_embedder; get_embedder()"

# Jalan sebagai user biasa; Chainlit perlu menulis ke /app/.chainlit & /app/.files
RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
