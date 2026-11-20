# Arsitektur

## Keputusan: agent dijalankan lewat apa?
**Pilihan A — graph dipanggil langsung di backend.**
FastAPI meng-`import` graph dari `src/agent`, compile dengan checkpointer Postgres, lalu `graph.astream(...)`.

Alasan:
- Satu container aplikasi + satu container Postgres. Paling sederhana untuk Docker dan deploy (tugas menilai URL yang jalan).
- Tidak butuh fitur Agent Server (assistants, cron, run queue).
- `langgraph dev` tetap dipakai saat development (Studio UI), dengan graph yang sama.

## Komponen

```
Browser ──HTTPS──▶ Nginx/Tunnel ──▶ app (FastAPI, port 8000)
                                     ├── /            UI Chainlit (di-mount ke FastAPI)
                                     ├── /api/chat    endpoint chat (SSE streaming)
                                     ├── /health      cek server hidup
                                     └── graph LangGraph ──▶ OpenRouter (model gratis)
                                              │
                                              └── checkpointer ──▶ db (Postgres 16)
```

- **UI**: Chainlit di-mount ke FastAPI (`chainlit.utils.mount_chainlit`), satu proses, satu port.
  Handler Chainlit memanggil fungsi `stream_reply()` yang sama dengan endpoint `/api/chat`.
- **API key** OpenRouter & LangSmith hanya di environment backend, tidak pernah ke browser.
- **Tanpa login/paket/limit/pembayaran** (langkah 12, 14, 15 di-skip; lihat PRD #13).

## Agent (graph)

### Sekarang (baseline, langkah 8)
```
START ──▶ call_model ──▶ END
```
| Node | Jenis | Tugas |
|---|---|---|
| `call_model` | LLM | System prompt + riwayat pesan → jawaban |

State (`src/agent/state.py`): `messages: Annotated[list[AnyMessage], add_messages]`.

### Rencana (langkah 9, **nanti**)
ReAct agent dengan tools:
```
START ──▶ call_model ──(ada tool_calls)──▶ tools ──▶ call_model
               └──(tidak ada)──▶ END
```
| Node/Tool | Jenis | Tugas |
|---|---|---|
| `search_darglobal` | Data | Retrieval teks halaman DarGlobal (hybrid BM25 + vector) |
| `search_wasalt_listings` | Data | Filter terstruktur iklan Wasalt |
| `wasalt_market_stats` | Data | Statistik harga per kota/tipe |

## Memory
- Checkpointer `AsyncPostgresSaver` (`langgraph-checkpoint-postgres`), tabel dibuat otomatis lewat `.setup()` saat start.
- `thread_id` = id sesi chat Chainlit (UI) atau dikirim client (`/api/chat`).
- Di `langgraph dev`, persistence diurus server dev-nya sendiri (graph di-export tanpa checkpointer).

## Konfigurasi (env)
`OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`, `OPENROUTER_MODEL`, `CHECKPOINT_DATABASE_URL`,
`LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, (`LANGSMITH_ENDPOINT`).

## Deploy
`docker compose up -d --build` di server: service `app` + `db` (volume `pgdata`), `restart: unless-stopped`.
HTTPS lewat Nginx/Cloudflare Tunnel di depan port 8000. **(nanti: pilih host & domain)**
