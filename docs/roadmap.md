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
- [ ] Isi `LLM_API_KEY` (DeepSeek) di `.env`, coba di `langgraph dev`, lihat trace di LangSmith

## Milestone 2: Kerangka produk
- [x] FastAPI + `/health` + config dari env
- [x] Checkpointer Postgres (memori per thread) + docker compose `db`
- [x] Endpoint `/api/chat` streaming (SSE)
- [x] UI Chainlit di-mount ke FastAPI
- [x] Dockerfile + docker-compose (app + db)
- [ ] Tes multi-turn manual di UI dengan key asli

## Milestone 3: Data & tools (nanti)
- [ ] Bersihkan data (outlier harga sewa, `rent_freq` objek, title kosong, sisa daftar negara)
- [ ] Tool `search_wasalt_listings` + unit test
- [ ] Tool `wasalt_market_stats` + unit test
- [ ] Index & tool `search_darglobal` (RAG) + unit test
- [ ] Ubah graph jadi ReAct (call_model ↔ tools)
- [ ] Finalisasi system prompt (PRD #6, #10, #11)

## Milestone 4: Eval & keamanan (nanti)
- [ ] Dataset eval (PRD #15) di LangSmith + evaluator
- [ ] Tes prompt injection & kebocoran system prompt
- [ ] Rate limit sederhana per IP (batasi biaya LLM)

## Milestone 5: Deploy (nanti)
- [ ] Pilih host + domain + HTTPS
- [ ] `.env` production terpisah
- [ ] Deploy `docker compose up -d --build`, cek URL publik
