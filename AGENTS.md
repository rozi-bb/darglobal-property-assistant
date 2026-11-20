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
- Deploy (laptop Ubuntu, `~/projects/darglobal-chatbot`): rsync kode lalu
  `docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production up -d --build`
  (detail & URL publik: `docs/architecture.md` bagian Deploy)
- Test:     `uv run pytest -q` (integration: `uv run pytest -q -m integration`)
- Lint:     `uv run ruff check src tests`
- Eval:     `uv run python -m evals.run` (LLM asli + DB lokal → `docs/eval-results.md`; `--upload` ke LangSmith)
- Data:     `uv run python -m dataprep.build` lalu `uv run python -m dataprep.load_db` lalu `uv run python -m dataprep.embed` (scrape ulang DarGlobal: `uv run python -m dataprep.darglobal`)

## Struktur
- Agent: Graph `src/agent/graph.py` (`create_agent`) · Middleware `src/agent/middleware.py`
  · Tools `src/agent/tools.py` + `wasalt.py` · Retrieval `src/agent/retrieval.py` · DB `src/agent/db.py`
  · LLM `src/agent/llm.py` · Prompt `src/agent/prompts.py`
- Backend: `src/server/main.py` (FastAPI) · `config.py` · `chat.py` (endpoint chat) · `ui.py` (Chainlit)
- Eval: `src/evals/` (dataset, evaluator, runner)
- Data: pipeline `src/dataprep/` · mentah `data/raw/` (tidak di-commit) · bersih `data/processed/` · Test: `tests/`
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
