# AGENTS.md

## Project
Chatbot LangGraph (Python 3.12) untuk tugas teknis DarGlobal: tanya-jawab soal proyek DarGlobal
dan pasar properti Saudi (data Wasalt). LLM DeepSeek API (kompatibel OpenAI, provider bisa diganti lewat env), jalan di Docker, di-deploy ke URL publik.
Konteks: `docs/prd.md` · Desain: `docs/architecture.md` · Task: `docs/roadmap.md`
Latar belakang lamaran (lokal saja, tidak di-commit): `CONTEXT.md`

## Commands
- Install:  `uv sync`
- Dev agent (Studio): `uv run langgraph dev`
- Dev app (FastAPI + UI Chainlit): `docker compose up -d db` lalu `uv run uvicorn server.main:app --reload --port 8000`
- Full stack (Docker): `docker compose up --build`
- Test:     `uv run pytest -q` (integration: `uv run pytest -q -m integration`)
- Lint:     `uv run ruff check .`

## Struktur
- Agent: State `src/agent/state.py` · Node `src/agent/nodes.py` · Tools `src/agent/tools.py`
  · LLM `src/agent/llm.py` · Prompt `src/agent/prompts.py` · Graph `src/agent/graph.py`
- Backend: `src/server/main.py` (FastAPI) · `config.py` · `chat.py` (endpoint chat) · `ui.py` (Chainlit)
- Data mentah: `data/` · Test: `tests/`
- Env DB memori: `CHECKPOINT_DATABASE_URL` (JANGAN pakai nama `DATABASE_URL`: Chainlit akan menyalakan data layer-nya sendiri)

## Rules
- Selalu pakai type hints.
- Tiap node baru wajib ada unit test.
- Test yang panggil API asli wajib `@pytest.mark.integration`.
- Assert ke STRUKTUR respons, bukan string persis.
- Jangan pakai API yang diawali `_` (internal LangChain).
- Jangan tambah dependency / ubah langgraph.json tanpa bilang.
- JANGAN PERNAH tulis nilai API key di file mana pun. Key hanya dari environment (`.env`).
- API key hanya di backend, tidak pernah dikirim ke UI/browser.
- Jawaban bot harus menyebut sumbernya (DarGlobal atau Wasalt); jangan campur iklan Wasalt seolah proyek DarGlobal.

## Workflow
- Kerjakan SATU task belum-tercentang dari roadmap.md per sesi.
- Sebelum koding fitur baru: JELASKAN DULU, tunggu aku bilang paham.
- Setelah selesai: centang task, jalankan test, lapor apa adanya,
  lalu BERHENTI DAN TUNGGU AKU.
