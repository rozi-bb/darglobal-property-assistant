# Arsitektur

> Status: desain target disetujui 20 Nov 2026. Kode masih baseline (`START → call_model → END`).
> Bagian **Target** dikerjakan bertahap lewat Milestone 3 di `roadmap.md`.

## Ringkasan
Satu **agent ReAct** (DeepSeek Flash) yang memilih sendiri tool yang dipanggil:
pencarian DarGlobal (hybrid search), pencarian iklan & statistik Wasalt (query tabel), atau tanya balik.
Agent boleh memanggil tool berkali-kali (maks 6 per pertanyaan). Draf jawaban dicek kode
(sitasi + harga); kalau gagal, agent memperbaiki (maks 2 revisi).
Dibangun dengan `create_agent` (LangChain v1) + middleware.

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
                                     └── agent (create_agent)
                                          ├── model ──▶ DeepSeek API (cadangan: OpenRouter)
                                          ├── search_darglobal ──▶ pgvector + BM25 (memori) + TypeSafe API (rerank Jev)
                                          ├── tools Wasalt ──▶ tabel wasalt_listings (query SQL)
                                          └── checkpointer ──▶ db (Postgres 16 + pgvector)
```

- **UI**: Chainlit di-mount ke FastAPI (`chainlit.utils.mount_chainlit`), satu proses, satu port.
  Handler Chainlit memanggil fungsi `stream_reply()` yang sama dengan endpoint `/api/chat`.
- **API key** (DeepSeek, OpenRouter, TypeSafe, LangSmith) hanya di environment backend, tidak pernah ke browser.
- **Tanpa login/paket/limit/pembayaran** (langkah 12, 14, 15 di-skip; lihat PRD #13). Tidak ada JWT.

## Agent (graph)

### Sekarang (baseline, langkah 8)
```
START ──▶ call_model ──▶ END
```
| Node | Jenis | Tugas |
|---|---|---|
| `call_model` | LLM | System prompt + riwayat pesan → jawaban |

State (`src/agent/state.py`): `messages: Annotated[list[AnyMessage], add_messages]`.

### Target: agent ReAct (`create_agent` + middleware)
```
pesan user
   │
   ▼
before_model: samarkan PII → ringkas riwayat → prompt dinamis
   │
   ▼
┌─▶ model: DeepSeek Flash ──tool_calls──▶ tools (maks 6 panggilan per run)
│        │        ▲                            │
│        │        └────────── hasil ───────────┘
│        │ draf jawaban (tanpa tool_calls)
│        ▼
└── after_model: cek jawaban (kode)
       gagal → catatan perbaikan, model mengulang (maks 2 revisi)
       lolos → jawaban final ke user
```

Agent memutuskan sendiri, tanpa classifier/router di depan:
- memanggil satu tool, beberapa tool sekaligus (mis. DarGlobal + Wasalt untuk perbandingan),
  atau mengulang dengan parameter lain kalau hasilnya kosong/salah;
- memanggil `ask_user` kalau pertanyaan ambigu (mis. kota atau jual/sewa tidak disebut);
- menjawab langsung tanpa tool untuk sapaan, atau menolak sopan kalau di luar topik (diatur system prompt).

### Middleware
| # | Middleware | Jenis | Tugas |
|---|---|---|---|
| 1 | `PIIMiddleware` (`email`, `credit_card`, `phone_number` via regex) | bawaan | Samarkan data pribadi di pesan user sebelum dikirim ke LLM |
| 2 | `SummarizationMiddleware` | bawaan | Ringkas riwayat lama kalau chat sudah panjang (hemat token) |
| 3 | `@dynamic_prompt` | bawaan (decorator) | Sisipkan tanggal hari ini + instruksi "jawab dalam bahasa user" ke system prompt |
| 4 | `ModelCallLimitMiddleware` | bawaan | Batas panggilan model per run (termasuk revisi) |
| 5 | `ToolCallLimitMiddleware` | bawaan | Maks 6 panggilan tool per run |
| 6 | `ModelFallbackMiddleware` | bawaan | DeepSeek gagal → model cadangan (OpenRouter) |
| 7 | `ModelRetryMiddleware` | bawaan | Retry + backoff saat API model error/timeout |
| 8 | `ToolRetryMiddleware` | bawaan | Retry tool yang gagal sementara (mis. timeout TypeSafe/DB) |
| 9 | `AnswerCheckMiddleware` | custom | Cek draf jawaban (lihat di bawah) |

Urutan di atas adalah urutan awal; urutan eksekusi hook dicek ulang saat implementasi.
Tidak dipakai: `HumanInTheLoopMiddleware` (untuk menyetujui aksi tool, sedangkan tools kita hanya membaca data),
`LLMToolSelectorMiddleware` (tool cuma 4), context editing, to-do list, shell/file search, deepagents.

### Middleware cek jawaban (custom)
- Hook `after_model` dengan `can_jump_to=["model"]`. Hanya jalan kalau pesan terakhir adalah AI tanpa `tool_calls` (draf final).
- Aturan, semuanya kode (tanpa LLM):
  1. Setiap URL di jawaban harus ada di hasil tool pada run ini.
  2. Setiap angka harga (SAR, AED, USD, …) harus ada di hasil tool (setelah normalisasi format angka).
  3. Sumber disebut: hasil `search_darglobal` dipakai → sebut "DarGlobal"; hasil tool Wasalt dipakai → sebut "Wasalt".
- Gagal dan revisi < 2 → tambahkan catatan perbaikan, `jump_to: "model"`. Draf yang gagal tidak ditampilkan ke user.
- Gagal 2x → jawaban aman: bilang belum bisa memastikan dari data + arahkan ke https://darglobal.co.uk/get-in-touch.
- Jawaban tanpa link/angka (sapaan, penolakan, tanya balik) otomatis lolos.
- LLM judge sengaja belum dipakai (hemat 1 panggilan LLM per pertanyaan). Ditambahkan kalau eval menunjukkan
  iklan Wasalt masih tercampur dengan proyek DarGlobal.

## Tools

| Tool | Jenis | Input utama | Output |
|---|---|---|---|
| `search_darglobal` | RAG, hybrid search | `query` (bahasa Inggris, berdiri sendiri), `country?` | ≤ 4 potongan teks + URL + nama proyek, atau `not_found` |
| `search_wasalt_listings` | query tabel | `city`, `property_type`, `purpose`, `min_price?`, `max_price?`, `bedrooms?`, `limit` (≤ 10) | daftar iklan + URL |
| `wasalt_market_stats` | query tabel | `city`, `property_type?`, `purpose` | `n`, median, p25–p75, median harga/m² |
| `ask_user` | interaksi (tanya balik) | `question`, `options` | jawaban user (lewat `interrupt`) |

- Semua input divalidasi Pydantic (kota/tipe/purpose = enum dari data). Docs DeepSeek mengingatkan argumen tool
  bisa tidak valid atau dikarang, jadi input salah dikembalikan sebagai pesan error berisi nilai yang valid,
  lalu agent mencoba lagi.
- Hasil tool selalu membawa field `source` (`"DarGlobal"` atau `"Wasalt"`) supaya sumber tidak tercampur.

### `search_darglobal`: hybrid search
```
query + filter negara (ditulis agent)
            │
     ┌──────┴──────┐
   dense         sparse
   pgvector      BM25
   bge-small     (di memori)
     └──────┬──────┘
            ▼
       RRF → top 20
            ▼
   rerank Jev: skor 0–1 per passage → top 4, skor ≥ 0.5
            │
     ┌──────┴───────────────┐
  tidak ada               ada
  not_found               4 potongan + URL → agent
  (tanpa panggil model)
```
- Agent menulis query lengkap dan berbahasa Inggris saat memanggil tool (mis. "cicilannya gimana?" →
  "Trump Tower Jeddah payment plan"), jadi node `rewrite_query` terpisah tidak perlu (hemat 1 panggilan LLM).
  Halaman DarGlobal berbahasa Inggris dan bge-small adalah model bahasa Inggris.
- Rerank: satu request Jev (TypeSafe), satu pertanyaan Noul per passage ("passage ini relevan dengan query?").
- Jev error/timeout → pakai top 4 dari urutan RRF (tanpa rerank), dicatat di trace. Pencarian tetap jalan.
- `not_found` bukan jalan buntu: agent bisa mencoba query lain, mencari di Wasalt, atau memanggil `ask_user`.
- Angka top 20 / top 4 / skor 0.5 adalah nilai awal, disetel lewat eval.

### Tools Wasalt
- Tools query SQL langsung ke tabel `wasalt_listings` (1.172 baris): filter = `WHERE`,
  statistik = `percentile_cont`. Tanpa pandas di app.
- `wasalt_market_stats` membuang outlier (IQR) dan selalu melaporkan jumlah sampel `n`; `n < 5` ditandai "sampel kecil".
- Wasalt tidak pakai RAG: pertanyaan harga/jumlah butuh angka pasti dari filter dan hitungan,
  bukan potongan teks yang "mirip".

### `ask_user` (tanya balik)
- Tool memanggil `interrupt()` LangGraph: run berhenti, state tersimpan di checkpointer.
- Chainlit menampilkan pertanyaan + tombol pilihan; jawaban user dikirim balik dengan `Command(resume=...)`, agent lanjut.
- `/api/chat` mengirim event SSE khusus berisi pertanyaan + pilihan; client melanjutkan dengan jawaban user.

## UI & streaming
- Selama run, Chainlit menampilkan langkah agent (`cl.Step`), mis. "Mencari di DarGlobal: …".
- Jawaban final dikirim setelah lolos cek jawaban, jadi draf yang gagal tidak pernah tampil.

## Pipeline data (offline) — `src/dataprep/`
Prinsip: ambil **JSON CMS bawaan halaman** (`__NEXT_DATA__`, Strapi), bukan teks layar → tanpa menu/teks dobel.
Tidak ada bypass CAPTCHA/anti-bot; patuhi robots.txt & llms.txt Wasalt; kontak pengiklan tidak diambil.

| Sumber | Cara ambil | Bersihkan | Hasil (`data/processed/`) |
|---|---|---|---|
| DarGlobal | sitemap resmi (481 URL) → CDX Wayback → snapshot asli `id_` terbaru non-challenge (situs live kena CAPTCHA Imperva); ≥4,5 dtk/request | JSON → teks markdown per seksi CMS, buang media/menu/tombol; potongan < 150 karakter digabung ke potongan tetangga di halaman yang sama; fallback scrape lama (Nov 2026) untuk halaman yang tidak terarsip | `darglobal_projects.jsonl` (terstruktur), `darglobal_chunks.jsonl` (RAG) |
| Wasalt | snapshot lama 19–20 Nov 2026 (scrape ulang diblokir Cloudflare untuk browser otomatis; tidak dipaksa) | Pydantic; kota diseragamkan; angka string → numerik; sewa → per tahun; buang harga 0/di luar batas wajar/ekstrem (IQR log); dedup; luas tak masuk akal dikosongkan; jumlah kamar tak masuk akal dikosongkan (apartemen > 10 kamar atau < 15 m²/kamar). Tanpa kolom status aktif: sitemap tersimpan tidak memuat iklan baru, jadi tidak andal | `wasalt_listings.jsonl`, `wasalt_rejected.jsonl` (+ alasan) |

- Ringkasan jumlah per sumber/jenis: `data/processed/manifest.json`. Data mentah (`data/raw/`) tidak di-commit.
- Tabel Postgres (DB yang sama dengan memori chat): `darglobal_projects`, `darglobal_chunks` (kolom `tsv` + index GIN
  sebagai cadangan; keyword search memakai BM25 di memori), `wasalt_listings`. Dimuat ulang (idempoten) dengan `dataprep.load_db`.
- Perintah:
  - `uv run python -m dataprep.darglobal` — scrape arsip (bisa dilanjutkan; ±45 menit dari nol)
  - `uv run python -m dataprep.build` — bersihkan → `data/processed/`
  - `uv run python -m dataprep.load_db` — muat ke Postgres (`CHECKPOINT_DATABASE_URL`)
- Berikutnya (task RAG): embed `darglobal_chunks` (bge-small) ke pgvector, gabung dengan BM25 di memori (RRF).

## Memory
- Checkpointer `AsyncPostgresSaver` (`langgraph-checkpoint-postgres`), tabel dibuat otomatis lewat `.setup()` saat start.
- `thread_id` = id sesi chat Chainlit (UI) atau dikirim client (`/api/chat`).
- Riwayat panjang diringkas `SummarizationMiddleware`; `ask_user` butuh checkpointer karena memakai `interrupt`.
- Di `langgraph dev`, persistence diurus server dev-nya sendiri (graph di-export tanpa checkpointer).

## Konfigurasi (env)
Sudah ada: `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `CHECKPOINT_DATABASE_URL`,
`LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, (`LANGSMITH_ENDPOINT`).

Untuk target (sudah ada di `.env.example`, belum dipakai kode):
- `TYPESAFE_API_KEY` — rerank Jev.
- `FALLBACK_LLM_API_KEY`, `FALLBACK_LLM_BASE_URL`, `FALLBACK_LLM_MODEL` — model cadangan (OpenRouter), opsional.

## Dependency untuk target (ditambahkan 20 Nov 2026)
| Paket | Untuk |
|---|---|
| `langchain` (v1) | `create_agent` + middleware (sebelumnya baru ada `langchain-core` dan `langchain-openai`) |
| `fastembed` | embedding bge-small lokal, tanpa API |
| `pgvector` | tipe `vector` di psycopg |
| `rank-bm25` | keyword search BM25 |
| `typesafe-sdk` | rerank Jev |

pandas tetap hanya di grup `dev` (notebook EDA); tools Wasalt memakai SQL.

## Rencana struktur file
- `src/agent/graph.py` — `create_agent(...)` + daftar middleware (menggantikan `call_model`)
- `src/agent/tools.py` — 4 tools
- `src/agent/retrieval.py` — hybrid search (pgvector, BM25, RRF, rerank Jev)
- `src/agent/middleware.py` — prompt dinamis + cek jawaban
- `src/agent/prompts.py` — system prompt
- `src/dataprep/` — scrape, bersihkan, chunk, muat ke Postgres (sudah ada); embed menyusul

## Deploy
`docker compose up -d --build` di server: service `app` + `db` (volume `pgdata`), `restart: unless-stopped`.
Image `db`: `pgvector/pgvector:pg16` (Postgres 16 + ekstensi `vector`, sejak 20 Nov 2026): satu database untuk memori
chat dan vektor. Pindah dari `postgres:16-alpine` memakai volume yang sama, lalu `REINDEX DATABASE` (beda collation libc).
HTTPS lewat Nginx/Cloudflare Tunnel di depan port 8000. **(nanti: pilih host & domain)**

## Keputusan

### Provider LLM
**DeepSeek API langsung** (`https://api.deepseek.com`, model `deepseek-flash`), bukan model `:free` OpenRouter seperti di email HR.
Alasan: rate limit model gratis OpenRouter terlalu kecil untuk testing, eval, dan penguji HR.
`llm.py` memakai `ChatOpenAI` dengan `base_url`, jadi pindah ke OpenRouter (atau provider lain) cukup ganti
`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` di `.env`. `deepseek-flash` mendukung tool calling berulang (docs DeepSeek).

### ReAct, bukan router di depan
Desain sebelumnya: classifier intent (Jev) + router if/else → satu jalur per pesan. Diganti agent ReAct karena:
- pertanyaan campuran (DarGlobal + Wasalt) dan pertanyaan lanjutan ditangani tanpa aturan tambahan;
- agent bisa mengulang pencarian kalau hasil kosong atau parameternya salah;
- sapaan dan pertanyaan di luar topik cukup diatur lewat system prompt, tanpa model terpisah.

Trade-off: minimal 2–3 panggilan LLM per pertanyaan dan perilaku lebih susah ditebak.
Ditahan dengan batas panggilan, validasi input tool, cek jawaban, dan eval.

### `create_agent` + middleware, bukan StateGraph manual
Loop ReAct dan fitur produksi (retry, fallback, batas panggilan, ringkasan riwayat, PII) sudah tersedia dan teruji,
jadi kode yang ditulis lebih sedikit. `create_agent` tetap menghasilkan graph LangGraph, jadi checkpointer Postgres,
streaming, LangSmith, dan `langgraph dev` tetap jalan seperti sekarang.

### Keyword search: BM25 di memori, bukan full-text Postgres
Full-text Postgres (`ts_rank`) tidak memakai IDF, jadi kata langka seperti nama proyek ("Rayana") tidak diberi
bobot lebih. BM25 dibangun di memori dari tabel `darglobal_chunks` saat app start (±2.700 chunk, cepat).
Data tetap satu sumber di Postgres. Wasalt tidak butuh ranking, jadi cukup query SQL.

### Jev hanya untuk rerank
Pemilihan jalur, sapaan, dan penolakan ditangani agent. Jev hanya dipakai menilai relevansi passage di
`search_darglobal`; kalau error, pencarian jatuh ke urutan RRF.

### Cek jawaban: kode dulu
Cek kode murah, cepat, dan tidak bisa "yakin sendiri", jadi menangkap link/harga yang dikarang.
LLM judge ditunda sampai eval membuktikan perlu.
