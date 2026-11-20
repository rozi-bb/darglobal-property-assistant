# Roadmap

Cara pakai tiap sesi: "Baca docs/roadmap.md, kerjakan task belum-tercentang berikutnya."

## Milestone 0: Fondasi
- [x] Scaffold (langgraph CLI) + git + .gitignore + .env.example
- [x] AGENTS.md, prd.md, architecture.md, roadmap.md
- [x] Tracing LangSmith lewat env

## Milestone 1: Agent polos (baseline)
- [x] State `messages` (state.py)
- [x] LLM via API kompatibel OpenAI, default DeepSeek (llm.py) + node `call_model` dengan system prompt (nodes.py)
- [x] Graph START → call_model → END (graph.py)
- [x] Unit test node pakai fake LLM
- [x] Isi `LLM_API_KEY` (DeepSeek) di `.env` dan tes chat asli
- [ ] Coba di `langgraph dev`, lihat trace di LangSmith

## Milestone 2: Kerangka produk
- [x] FastAPI + `/health` + config dari env
- [x] Checkpointer Postgres (memori per thread) + docker compose `db`
- [x] Endpoint `/api/chat` streaming (SSE)
- [x] UI Chainlit di-mount ke FastAPI
- [x] Dockerfile + docker-compose (app + db)
- [x] Tes multi-turn dengan key asli (API + UI)

## Milestone 3: Data & tools (selesai 20 Nov 2026) — desain di `architecture.md`
- [x] Infra (izin dulu): dependency baru, image db → `pgvector/pgvector:pg16`, env baru di `.env.example`
- [x] Bersihkan data (outlier harga sewa, `rent_freq` objek, title kosong, sisa daftar negara)
- [x] Ambil ulang DarGlobal dari JSON CMS (Wayback) + pipeline `src/dataprep/` + muat ke Postgres
- [x] Tool `search_wasalt_listings` + unit test
- [x] Tool `wasalt_market_stats` + unit test
- [x] Index hybrid DarGlobal: chunk + embed bge-small ke pgvector + index BM25
- [x] Tool `search_darglobal` (RRF + rerank Jev + fallback RRF) + unit test
- [x] Tool `ask_user` (tanya balik lewat `interrupt` + tombol Chainlit) + unit test
- [x] Ganti graph ke `create_agent` + middleware bawaan (PII, ringkasan, prompt dinamis, retry, fallback, batas)
- [x] Middleware cek jawaban (link + harga + sumber, maks 2 revisi) + unit test
- [x] Chainlit: tampilkan langkah agent, jawaban final setelah lolos cek
- [x] Finalisasi system prompt (PRD #6, #10, #11)

## Milestone 4: Eval & keamanan (selesai 20 Nov 2026)
- [x] Dataset eval (PRD #15) di LangSmith + evaluator — 24 kasus (termasuk 9 serangan), 24/24 lolos (`docs/eval-results.md`);
      `--upload` ke LangSmith belum dites (`LANGSMITH_API_KEY` kosong)
- [x] Tes prompt injection & kebocoran system prompt — pagar kebocoran + allowlist domain link di middleware,
      `tests/unit_tests/test_security.py` (15 test), eval `s1`–`s9` (model menolak sendiri, pagar tidak terpicu)
- [x] Rate limit sederhana per IP (batasi biaya LLM) — 20 pesan/10 menit per IP + 1000/hari global, API (429) & UI;
      `tests/unit_tests/test_ratelimit.py`

## Milestone 5: Deploy (selesai 20 Nov 2026)
- [x] Pilih host + domain + HTTPS — laptop Ubuntu 24 jam + Tailscale Funnel (tanpa domain sendiri):
      https://rozi.tail7d5bfc.ts.net:10000/
- [x] `.env` production terpisah — `.env.production` di laptop (tidak di-commit) + `docker-compose.prod.yml`
- [x] Deploy `docker compose up -d --build`, cek URL publik — data dimuat + embed di container; event pertama ±1 dtk
      dari internet
