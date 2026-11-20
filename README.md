# DarGlobal Property Assistant

Chatbot LangGraph untuk tanya-jawab proyek DarGlobal dan pasar properti Saudi (iklan Wasalt).
LLM: DeepSeek API (bisa diganti ke OpenRouter lewat env) · FastAPI + UI Chainlit · memori chat di Postgres · Docker Compose.

## Jalankan (Docker)

```bash
cp .env.example .env   # isi LLM_API_KEY (key DeepSeek) (dan LANGSMITH_API_KEY kalau mau tracing)
docker compose up -d --build
```

Buka http://localhost:8000 (diarahkan ke UI di `/chat`).

| Path | Isi |
|---|---|
| `/chat` | UI chat (Chainlit) |
| `/api/chat` | `POST {"message": "...", "thread_id": "opsional"}` → stream SSE: `start`, `token`, `error`, `end` |
| `/health` | cek server hidup |

## Development

```bash
uv sync
docker compose up -d db                               # Postgres saja
uv run uvicorn server.main:app --reload --port 8000   # app di host
uv run langgraph dev                                  # Studio UI untuk debug graph
uv run pytest -q                                      # unit test (pakai fake LLM)
uv run pytest -q -m integration                       # panggil DeepSeek asli
```

## Dokumen

- `AGENTS.md` — aturan kerja di repo ini
- `docs/prd.md` — apa & kenapa
- `docs/architecture.md` — bagaimana
- `docs/roadmap.md` — urutan & progres
