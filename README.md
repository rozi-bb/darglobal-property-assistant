# DarGlobal Property Assistant

An AI chatbot that answers questions about **DarGlobal projects** and the **Saudi property market** (Wasalt
listings). Built with LangGraph / LangChain v1, DeepSeek, FastAPI + Chainlit and Postgres (pgvector), shipped with
Docker Compose.

**Live demo: https://rozi.tail7d5bfc.ts.net:10000/**

Try for example:
- "What is Trump Tower Jeddah?"
- "Which DarGlobal projects are in Saudi Arabia?"
- "Show me 3-bedroom apartments for rent in Riyadh under 80,000 SAR"
- "What's the typical price of a villa for sale in Jeddah?"
- "Compare Rayana with villas on the market in Riyadh."
- "I want an apartment, what's available?" (the bot asks back with buttons)

## What it does

- **One ReAct agent** (`create_agent` + middleware) that picks its own tools, up to 6 tool calls per question:
  - `search_darglobal`: hybrid search over DarGlobal website content (pgvector embeddings + BM25, fused with RRF,
    optional rerank).
  - `search_wasalt_listings`: filtered SQL search over Wasalt listings (city, buy/rent, type, bedrooms, budget).
  - `wasalt_market_stats`: median price, p25–p75 range, price per m² and sample size, with outliers removed.
  - `ask_user`: a clarifying question with button options (LangGraph `interrupt`).
- **Every answer names its source** (DarGlobal or Wasalt) and keeps them apart. Wasalt listings are never presented
  as DarGlobal properties. DarGlobal does not publish unit prices, so the bot points to DarGlobal sales instead of
  guessing.
- **Code-checked answers.** Before the user sees a draft, middleware checks it without an extra LLM call:
  - every link must come from this conversation's tool results and point to darglobal.co.uk or wasalt.sa;
  - every price must match a number in the data;
  - the source must be named.

  A failing draft is sent back for revision (up to 2 times) and is never shown. If it still fails, the user gets a
  safe fallback answer.
- **Chat memory** per conversation (Postgres checkpointer), with automatic summarisation of long histories.
- **Streaming UI**: Chainlit shows each agent step ("Wasalt market statistics: Jeddah · Villa · sale"), then the
  final answer. It replies in the user's language (tested with English and Indonesian).

## Quality and safety

Evaluation set: 24 cases in `src/evals/dataset.jsonl`, with code evaluators plus an LLM judge. Results are in
[`docs/eval-results.md`](docs/eval-results.md).

| Metric | Result |
|---|---|
| Correct and sourced | 24/24 (100%) |
| Answers with invented links or prices | 0 |
| First streamed event (p50) | 1.35 s locally; ~1 s on the live URL |
| Final answer (p50) | 3.7 s |

- **Prompt injection and leak guard.** Before an answer or a clarifying question reaches the user, it is checked
  for:
  - overlap with the system prompt;
  - internal tool names;
  - secret env names and key-like strings.

  Anything that matches is replaced by a polite refusal. The eval includes 9 attack cases: instruction translation,
  "developer mode", API-key requests, base64 payloads, two-step extraction, an Indonesian-language injection, a fake
  price to confirm, Wasalt presented as DarGlobal, and a competitor ad. Unit tests also cover injection hidden
  inside tool results (`tests/unit_tests/test_security.py`).
- **Rate limit.**
  - Per IP: 20 messages per 10 minutes.
  - Global: 1,000 messages per day, a cost cap in case the URL gets shared.
  - Applies to both the API (HTTP 429 + `Retry-After`) and the UI.
- **API keys stay on the backend**; nothing secret is sent to the browser.

## Data

| Source | How it was collected | Result |
|---|---|---|
| DarGlobal (darglobal.co.uk) | Official sitemap → archived copies on the Wayback Machine (the live site is behind bot protection, which was not bypassed). Text comes from the CMS JSON embedded in each page, so there is no menu or button noise. | 40 projects, 2,701 text chunks (projects, blog, press). Most pages are from the 31 May 2026 snapshot. |
| Wasalt (wasalt.sa) | Listing snapshot from 19–20 Nov 2026, respecting robots.txt / llms.txt; advertiser contact details are not stored | 1,172 clean listings in 10 cities (135 rejected: duplicates, empty or implausible prices) |

Cleaned data is in `data/processed/` (`manifest.json` has the counts). The pipeline is in `src/dataprep/`.
Answers about Wasalt mention the snapshot date, because prices and availability change.

## Architecture

```
Browser ──HTTPS──▶ app (FastAPI, port 8000)
                    ├── /chat       Chainlit UI (/ redirects here)
                    ├── /api/chat   chat endpoint (SSE)
                    ├── /health
                    └── agent (create_agent + middleware)
                         ├── LLM ──▶ DeepSeek API (OpenAI-compatible; optional fallback model)
                         ├── search_darglobal ──▶ pgvector + BM25 (+ optional rerank)
                         ├── Wasalt tools ──▶ wasalt_listings table (SQL)
                         └── checkpointer ──▶ Postgres 16 + pgvector
```

The UI and the API call the same `run_agent()` function. Details and design decisions are in
[`docs/architecture.md`](docs/architecture.md) (written in Indonesian).

## Run it locally (Docker)

```bash
cp .env.example .env          # set LLM_API_KEY (DeepSeek key); other keys are optional
docker compose up -d --build
# One-time: load the data into Postgres and embed it (~2 minutes)
docker compose exec app python -m dataprep.load_db
docker compose exec app python -m dataprep.embed
docker compose restart app    # rebuild the in-memory keyword index from the loaded data
```

Open http://localhost:8000. You are redirected to the chat UI.

## API

`POST /api/chat` with `{"message": "...", "thread_id": "optional"}` returns a Server-Sent Events stream:

| Event | Data |
|---|---|
| `start` | `{thread_id}`: send it back to continue the conversation |
| `step` | `{tool, label, args}`: the agent is calling a tool |
| `ask` | `{question, options}`: the agent asks back; send the answer as the next `message` in the same thread |
| `answer` | `{text}`: final answer (after the answer check) |
| `error` | `{message}` |
| `end` | `{}` |

If the rate limit is hit, the endpoint returns HTTP 429 with `{error, retry_after_s}`.

```bash
curl -N https://rozi.tail7d5bfc.ts.net:10000/api/chat \
  -H 'Content-Type: application/json' -d '{"message": "What is Trump Tower Jeddah?"}'
```

## Development

```bash
uv sync
docker compose up -d db                               # Postgres only
uv run uvicorn server.main:app --reload --port 8000   # app on the host
uv run langgraph dev                                  # LangGraph Studio for the same graph
uv run pytest -q                                      # unit tests (fake LLM, no API calls)
uv run pytest -q -m integration                       # calls the real DeepSeek API
uv run ruff check src tests
uv run python -m evals.run                            # eval → docs/eval-results.md (--upload to LangSmith)
```

Data pipeline:
- `uv run python -m dataprep.darglobal`: re-scrape the archive.
- Then `dataprep.build`, then `dataprep.load_db`, then `dataprep.embed`.

## Configuration

See [`.env.example`](.env.example). Main variables:

| Variable | Purpose |
|---|---|
| `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` | Main model (default DeepSeek; any OpenAI-compatible provider works) |
| `FALLBACK_LLM_*` | Optional fallback model (e.g. OpenRouter) |
| `TYPESAFE_API_KEY` | Optional rerank for DarGlobal search (empty = RRF order) |
| `LANGSMITH_*` | Optional tracing |
| `CHECKPOINT_DATABASE_URL` | Postgres for chat memory and data (set automatically inside Docker Compose) |
| `RATE_LIMIT_PER_IP`, `RATE_LIMIT_GLOBAL_PER_DAY`, `TRUST_PROXY_HEADERS` | Rate limiting |

## Deployment

The live demo runs on a self-hosted Ubuntu machine:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production up -d --build
```

- The production override keeps Postgres off the host network and binds the app to `127.0.0.1` only.
- Public HTTPS comes from Tailscale Funnel, so there is no open router port and no own domain is needed.

## Known limitations

- Data is a snapshot, not live. Most DarGlobal content is from the May 2026 web archive; Wasalt listings are from
  19–20 Nov 2026.
- DarGlobal does not publish unit prices or payment-plan details for most projects, so the bot refers users to
  DarGlobal sales.
- The rate limit is kept in memory: one container, and counts reset on restart.
- The LLM judge uses the same model as the agent, so the code evaluators remain the main quality gate.
- The live demo runs on a single home server, so there is no redundancy.

## Project docs (Indonesian)

- `docs/prd.md`: requirements
- `docs/architecture.md`: design and decisions
- `docs/roadmap.md`: milestones and progress
- `AGENTS.md`: working rules for coding agents in this repo
