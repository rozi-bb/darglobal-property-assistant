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

## Milestone 4: Eval & keamanan (nanti)
- [x] Dataset eval (PRD #15) di LangSmith + evaluator — 15 kasus, 15/15 lolos (`docs/eval-results.md`);
      `--upload` ke LangSmith belum dites (`LANGSMITH_API_KEY` kosong)
- [ ] Tes prompt injection & kebocoran system prompt
- [ ] Rate limit sederhana per IP (batasi biaya LLM)

## Milestone 5: Deploy (nanti)
- [ ] Pilih host + domain + HTTPS
- [ ] `.env` production terpisah
- [ ] Deploy `docker compose up -d --build`, cek URL publik
