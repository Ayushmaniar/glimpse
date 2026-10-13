# How Glimpse works

The technical companion to the [README](../README.md): what is indexed and when, how results are ranked and
cut, and the measurements behind each number. Measurements were taken on the development laptop
(Windows 11, RTX 4070 Laptop GPU, 16-thread CPU) unless stated otherwise.

## Using it

Alt+Space shows or hides the window. If Alt+Space is taken, it falls back to Ctrl+Shift+Space, then
Alt+Shift+Space (see `.data\glimpse.log`). It opens as just a search bar and grows as results arrive.
Inside the window: ↑↓←→ move, Enter opens, Ctrl+Enter shows in folder, **Shift+Space previews** (PDF pages
with ←/→, photos, the video frame, the passage around a document or code hit), Ctrl+1…9 jump to a section,
Ctrl+M voice, Ctrl+D debug scores, Esc closes the preview, then clears the query, then hides.
**Move the window** by dragging the empty space around the search box (a grip appears at the top on hover)
or the footer; it reopens where you left it. Double-click those areas to put it back in the centre.

**Search this folder** (`explorer.py`): press Alt+Space while a File Explorer window (or the desktop) is in front
and the bar shows a **📁 Folder** chip, like Explorer's own search box. Results then come only from files inside
that folder **and all its subfolders** (settings, emoji and windows are hidden). The folder's whole tree is listed in
the background as the launcher opens (220k files in 0.9 s on the development laptop; until then a breadth-first walk), and
file names are matched against it on every keystroke (~80 ms), so any
file type is found by name (zip, exe, installers), even in folders Glimpse doesn't index ("Search file names
in …"). **Tab** (or a click on the chip) switches between the folder and **This PC**. Windows 11 Explorer tabs
work: the active tab's folder is used.

The **tray icon** (magnifier) shows indexing progress and has Open, Pause indexing, **Start with Windows**
(a per-user Run entry, off until you tick it), Open log and Quit. The backlog also pauses by itself on battery
(`pause_on_battery`) and lets the laptop sleep while paused; new files are still indexed (a few files are cheap).

For development, `.venv\Scripts\python.exe -m glimpse.app --browser` prints a URL instead of opening a window.

**What it indexes:** `Documents, Desktop, Downloads, Pictures, Videos, Music` (edit `folders` in
`.data\settings.json`; OneDrive folders work too). The first index runs in two passes: files changed in the
last 90 days first (searchable within the hour), then everything older. Within a pass it goes newest-first.
Searches always jump ahead of indexing work on the GPU, so the launcher stays fast (~40–90 ms) while the index
builds. The laptop is kept awake while indexing.

**Day to day** (`fresh.py`): the folders are watched (ReadDirectoryChangesW), so a new screenshot, download or
saved document is searchable **~2–4 s** after it is written, even while the first full index is still running:
new files take the GPU ahead of the backlog (searches still come first).

| Event | What happens |
|---|---|
| file created / saved | indexed once it has been quiet 3 s (downloads and saves finish first) |
| file saved again and again (a log, a notebook) | at most once per 2 min, always with the latest content |
| partial download (`.crdownload`, `.part`, `~$` Office temp) | ignored until it gets its real name |
| rename / move inside the folders | vectors kept, only the path changes (a renamed 5,000-photo folder costs no GPU time) |
| moved out of the folders, deleted, folder deleted | gone from results at once |
| frames of an image sequence | left to the periodic scan, which samples them |

The periodic scan stays as a safety net (changes made while the app was closed, buffer overflows, network
drives): hourly (`rescan_minutes`), or every 15 min if the watcher can't start. A full re-walk stats every file
(~5–10 s of disk time per source here), which is why it is not the main mechanism. With **Start with Windows** on,
the only catch-up scan is the one at logon.

Skipped on purpose: OneDrive files that are cloud-only (opening them would download them), Glimpse's own
folder, `node_modules`/`.git`/venvs/`AppData`, and hidden folders. Chunk budget per file: machine data
(`json, csv, log, xml, yaml, toml, ini`) and text files over 3 MB → first 2 chunks only (enough to find the file);
code and HTML → 60 chunks; prose, PDFs, Word and slides → 200.
Numbered frame sequences (`frame_0001.png`, `f_12.jpg`, `step_3.png`, `000123.png`, ≥50 in one folder: datasets,
renders, game captures) are sampled 1-in-20 (`frame_sample_every`). On the development laptop that was 55.8k of 88k images,
all inside project folders; camera, WhatsApp and screenshot names never match. Photo indexing went from ~3.4 h to ~1.2 h.

**Privacy:** shell history and browser history are **off** by default. Turn them on in `.data\settings.json`
(`"sources": {"shell_history": true, "browser_history": true}`). Even when they're on, commands that look
like they contain secrets are never indexed, and login, checkout and session-token URLs are skipped. The local
HTTP API only listens on 127.0.0.1 and requires a per-run random token, so web pages can't drive it.

**API keys and passwords** (`secretmask.py`): files that hold them are indexed like any other, so "gemini api key"
finds the `.env` or note with it (`.env`, `.env.local`, `.env.production`, `prod.env` are indexed as text). The
result row shows the key masked, `GEMINI_API_KEY=AIza••••••5Gh`, so it isn't on screen during a screen share;
**Copy key** copies the exact value and Shift+Space shows the file as it is. Detected: Google `AIza…`, OpenAI
`sk-…`, Anthropic `sk-ant-…`, GitHub, AWS, Slack, Stripe, Hugging Face, JWTs, private-key blocks, URL passwords,
`Bearer` tokens, `KEY=`/`password:` assignments with literal values (not `os.environ[...]` or placeholders), and
unlabelled random 32+ character tokens (~95% of them; hex hashes, UUIDs, CamelCase names and URLs are left alone).
Like the files themselves, the index stores the text unmasked, on this disk only.
When a query asks for a key ("gemini api key", "database password", "openai key"), a document holding a detected
key goes first and the documents section leads; naming a provider (gemini, openai, aws, github, …) limits this to
that provider's keys, and a bare "key" ("key points") never triggers it.

## How it decides what to show

1. The query is embedded once (~23 ms on the RTX 4070).
2. Every source (settings, documents, photos, recordings, video, windows, emoji, history) ranks its own items.
3. Each section gets an **intent** score (query vs. a one-line description of the source, softmax) and a
   **relevance** score (how far its best hit clears that source's noise floor). Image↔text, audio↔text and
   text↔text similarities live on different scales, so each source has its own measured `floor`/`strong`.
4. Sections are ordered by `0.5·intent + 0.5·relevance`. A section shows only results above its floor and
   within 0.10 of its best hit. Status cards appear only for confident matches.
   Floors rise with index size (`floor_per_decade` per 10× items), because a bigger index has higher-scoring
   near-misses for nonsense queries. At 238k document chunks that bar (0.807) started cutting real matches
   (a two-word query whose exact document scored 0.795).
   **Documents are hybrid: embeddings + keywords.** Every chunk's full text is also in a SQLite FTS5 index
   (BM25, Porter stemming, accents folded). The embedding top 300 and the BM25 top 200 are merged by
   reciprocal-rank fusion (`1/(60+rank)` from each list), so an exact name, ID or rare word ranks high even when
   its embedding is mediocre. A document is shown if it clears the embedding floor **or** holds most of the
   query's *information*: each word is weighted by its IDF, `ln(1 + (N − df + ½)/(df + ½))` (so "the" ≈ 0.1 nats,
   a rare surname ≈ 9), and the document must contain ≥ 80 % of the query's total and ≥ 2.3 nats (`LEX_COVER`,
   `LEX_INFO` in core.py). No stop-word lists or word-count rules: common words simply carry no weight, and
   nonsense or absent topics have no document holding their rare words.
   Searching one folder uses the folder's own item count for the lift. The search response carries `diag` per
   source (floor used, best score, kept/cut; with `debug: true` also the score distribution and the fused
   candidates) for debugging. `python -m tests.test_ranking`, `python -m eval.run_cutoff_eval [--hybrid|--live]`.

   File names are indexed as people type them: `NorthwindGamesOffer2024.pdf` also as "Northwind Games Offer 2024"
   (camelCase and letter-digit joins split; the tokenizer already splits on spaces, `_`, `-`, `.`).

   *How the rule was chosen* (`eval/run_cutoff_eval.py`, on the development laptop's index, counts only): 60 queries made
   from document titles, 60 seven-word phrases from document text, and ~40 everyday topics verified absent from
   the index. Success = the document is shown; for absent topics, anything shown is a failure.
   Sweep of the keyword rule (replayed on the app's fused candidates):
   | keyword rule | title | phrase | absent shown |
   |---|---|---|---|
   | none (embedding floor only) | 21/60 | 6/60 | 0/39 |
   | cover ≥ 0.6 | 33/60 | 28/60 | 3/39 |
   | **cover ≥ 0.8 (shipped)** | 33/60 | 27/60 | **0/39** |
   | cover ≥ 1.0 | 33/60 | 26/60 | 0/39 |

   Adaptive cutoffs (top − p99.9, top − p99, z-score; best setting each) did worse than the fixed floor and were
   dropped. What the app actually shows, end to end (`--live`):
   | | title | phrase | absent shown |
   |---|---|---|---|
   | before (embedding floor + a word-matching patch) | 36/60 | 9/60 | 1/40 |
   | hybrid BM25 + embeddings | 33/60 | 27/60 | 0/39 |
   | + file names split into words (shipped) | **45/60** | **29/60** | **0/39** |

5. **Time** (`timeq.py`): date words are removed before embedding and become a window on each item's date
   (EXIF taken date for photos, otherwise modified time; visit time for history): today, yesterday, this/last
   week|month|year, past 3 days, 2 weeks ago, in March, March 2025, in/since 2024, on Monday, latest/newest/recent.
   Inside the window, results are ordered by similarity with a small recency tiebreak (+0.015, half-life 180 days)
   or newest-first for "latest …" and browse queries ("photos from yesterday", "recent"). Floors always use
   the raw similarity, so recency can reorder results but never lift noise into view. "latest …" looks at the
   last 30 days first and widens to a year, then everything. `python -m tests.test_time` (14 checks).

## Choosing the numbers (evidence)

Sources: the Prompt Engineering walkthrough of EmbeddingGemma 2 (Colab, T4, 1,000 Flickr photos, ESC-50
sounds, Big Buck Bunny, the "Attention" paper), plus benchmarks run **on the development laptop** (`eval/`, scratch scripts).

| Decision | Evidence | Choice |
|---|---|---|
| **Dimensions** | Video, photo R@1 / R@5: 768 → 86.4 / 97.5 · 512 → 86.1 / 97.2 · 256 → 84.2 / 96.7 · 128 → 75.2 / 92.3. Ours (settings router hit@1): 768 0.715 · 256 0.715 · 128 0.620 | **256** (3× smaller; 128 falls off a cliff) |
| **Storage format** | int8 + per-vector scale vs float: identical top-1 on every test, max score error 0.0026 | **int8 on disk** (260 B/vector vs 3,072 B naive) |
| **In-RAM format** | 1M×256 search: int8→float per query 224 ms · float32 resident 18 ms | **float32 in RAM** (1 KB/vector) |
| **Image detail** (vision tokens) | Video: 4× tokens = ~9× slower, same results. Ours: 70/140/default tokens = same speed, same top-1 | **default** |
| **GPU use while indexing photos** | 86% average GPU utilisation at ~80 W, CPU 16%: decoding keeps up, the model is the limit. Batching images gives identical vectors at the same ms/image | one image per call; save time by **not embedding frame dumps** instead |
| **Image size fed to model** | full-res 4K JPEG 310 ms/img · resized to 768 px 159 ms/img (cosine 0.994 to full-res) | **768 px** + JPEG draft decoding, 4-thread prefetch |
| **Audio** | Speech works (our voice-note tests: right file first every time). Sound effects weak (video: 24% top-1 on ESC-50; ours: alarm vs ring vs tada ≈ tie). GPU 22× real time vs CPU 7× | Index **speech**, 30 s windows, **GPU** |
| **Video** | Video: 5 s clips at ~1 fps work for moment search; a frame costs 140 tokens vs 280 for an image | 10 s clips × 2 frames (512 px) + 30 s speech windows; ≤360 clips/file |
| **Thresholds** | Video: scores cluster at 0.5–0.7, don't use one global cutoff. Ours: per-source floors measured on test corpora | per-source `floor`/`strong` |
| **Settings router** | 189 eval queries written apart from the catalog (`eval/actions_eval.toml`): description only 0.598 / 0.793 → hybrid + hubness 0.743 / 0.905 (hit@1 / hit@3) | hybrid (joined + best-phrase) − 0.5·hubness |
| **Model in fp16?** | The video warns about NaNs in fp16 with PyTorch. Not an issue here: LiteRT runs its own quantized graph | n/a |

Re-run the router eval with `.venv\Scripts\python.exe -m eval.run_actions_eval`.

## Storage & time to embed *everything* (napkin math for the development laptop)

Inventory of C:\ (totals only), skipping Windows, Program Files, AppData, node_modules, .git and venvs.
"Personal" means Documents, Desktop, Downloads, Pictures, Videos, Music, OneDrive.

| | Personal folders | Whole drive |
|---|---|---|
| Photos/screenshots (≥8 KB) | 80,065 | 111,983 |
| PDFs | 4,007 (54k pages, 21% scanned) → 20.8k chunks | 4,154 → 22.7k chunks |
| Word/PowerPoint | 623 → 30.2k chunks | 753 → 41.8k chunks |
| Text/Markdown/HTML/etc. | 28,420 files → 642k chunks | 87,645 → 1.37M chunks |
| Code | 21,071 files → 254k chunks | 179,094 → 2.56M chunks |
| Audio | 15,775 files, 88 h → ~18k windows | 17,203, 100 h → ~20k |
| Video | 4,164 files, 75 h → ~40k clips + speech windows | 4,614, 80 h → ~43k |
| **Vectors** | **≈ 1.09 M** | **≈ 4.2 M** |

| Format per vector | Personal (1.09M) | Whole drive (4.2M) |
|---|---|---|
| 768-d float32 (naive) | 3.3 GB | 12.8 GB |
| 768-d float16 | 1.7 GB | 6.4 GB |
| 256-d float16 | 0.56 GB | 2.1 GB |
| **256-d int8 (chosen)** | **0.28 GB** | **1.08 GB** |
| 128-d int8 (too lossy) | 0.14 GB | 0.55 GB |

**Whole index on disk** (vectors, plus metadata and 600-char snippets for text, plus ~8 KB thumbnails for photos and video clips):
**≈ 1.8 GB for personal folders** (about 1.3% of the ~137 GB of personal media/docs it covers) and
**≈ 4.8 GB for the whole drive**. Snippets and thumbnails dominate, not vectors. Trimming snippets to
240 chars and de-duplicating paths would bring personal down to ~1.4 GB.
**RAM while running:** ~1.1 GB (personal) / ~4.2 GB (everything). **Query time:** ~20 ms / ~75 ms brute force.

**One-time embedding time on the RTX 4070** (measured rates: text chunk 37 ms, image ~0.18 s pipelined,
audio and speech 22× real time, video clip ~0.3 s):

| | Personal | Whole drive |
|---|---|---|
| Documents + code | ~9.7 h | ~41 h |
| Photos | ~4.0 h | ~5.6 h |
| Audio | ~4.0 h | ~4.5 h |
| Video (visual + speech) | ~5.8 h | ~6.3 h |
| **Total** | **≈ 24 h** | **≈ 57 h** |

**Measured on the real personal folders** (Documents, Desktop, Downloads, Pictures, Videos, Music, local OneDrive files),
with the chunk budgets above: ~250k text chunks (3.8 h at the measured 18 chunks/s), 88k photos (~4.4 h),
15k audio files (~4 h), 1.3k videos (~2 h) → **≈ 14 h and ≈ 370k vectors** (~0.1 GB of vectors, ~1 GB index
with thumbnails, ~0.4 GB RAM). The budgets are what removed the bulk-data cost described below.

About 120 W × 24 h ≈ 2.9 kWh, roughly ₹25 of electricity, and no API costs. Text is the long pole, and most of it
is bulk data (1.2 GB of .txt/.html/.json in personal folders) and other people's code. Excluding those
folders cuts the personal total to roughly 15 h.

## What becomes possible (and what's built)

The model puts text, images, video frames and speech into **one** space, so "search by description" stops
being limited to file names. Built here:

- **Fix-it router**: describe a symptom in any language, get the right Settings page/tool plus live diagnostics.
- **Photos and screenshots by content**, including text inside them (error messages, receipts, slides) with no OCR.
- **Query by image** and **find similar**: duplicates, the original of a crop, the same scene in a video.
- **Recordings by what was said**: no transcription pipeline, nothing lost to a bad transcript.
- **Video moments** by what's on screen *or* said, opened at the timestamp.
- **Documents and code by meaning**: PDFs (page-accurate), Word, PowerPoint slides, notebooks, source code.
- **Open windows by what you're doing**, **emoji/symbols by meaning**, and opt-in **commands I ran** / **pages I visited**.
- **Voice queries** (Whisper) and **Hinglish**/multilingual queries.

Next, in rough order of value:
1. ~~Scanned PDFs as page images~~ **built** (`sources/scans.py`): pages without a text layer are rendered with
   PDFium at 768 px and embedded as images (≤60 per PDF). On synthetic scans (lease, vaccine certificate,
   handwritten list, bank statement) every query found the right page first; unrelated queries stay ≤0.62,
   under the 0.68 floor (`python -m tests.test_scans`). Next: PowerPoint slides as images (needs Office or LibreOffice to render).
2. **Zero-shot auto-albums**: label every photo as receipt / document / meme / screenshot / ID card by comparing
   it with text labels (no training), then filter ("all my receipts from 2025").
3. **Clipboard history by meaning** ("that link I copied this morning").
4. **App-specific catalogs**: Chrome `chrome://settings/...`, VS Code commands, Office options, using the same router as Windows Settings.
5. **Opt-in screen memory**: embed a downscaled screenshot of the active window every few minutes
   ("the chart I saw on Tuesday"). Recall-style, so strictly opt-in and local.
6. **Scale-up**: swap brute-force search for an int8 HNSW index (e.g. usearch) past a few million vectors.

## Layout

```
glimpse/
  app.py          window (pywebview) + Alt+Space hotkey + single instance
  server.py       127.0.0.1 HTTP API (token-protected) + UI files
  core.py         sources, background indexer, section ranking
  embedder.py     EmbeddingGemma 2 (LiteRT-LM), prefixes, Matryoshka truncation
  store.py        SQLite (int8 vectors + metadata) + float32 RAM matrices
  voice.py        faster-whisper, GPU with CPU fallback
  previews.py     on-demand thumbnails: matching PDF page, audio waveform (cached in .data/thumbs)
                  and Quick Look images / text context (RAM cache only)
  timeq.py        date and file-type words in queries -> time window, newest-first, type filter
  fresh.py        folder watcher: new/changed files indexed in seconds, renames keep vectors, deletes drop
  autostart.py    Start with Windows (HKCU Run entry), toggled from the tray
  explorer.py     the folder open in File Explorer (COM, Windows 11 tabs) for "search this folder"
  secretmask.py   finds API keys / tokens / passwords so snippets show them masked
  tray.py         notification-area icon: progress, pause, start with Windows, log, quit
  sources/        actions (+ live widgets), files, scans, images, audio, video, windows, emoji, names,
                  shell_history, browser_history; walk.py (incremental sync), media.py (ffmpeg),
                  pdfpages.py (PDFium rendering)
  catalog/actions.toml   95 Windows actions — add your own
  ui/             index.html, app.js, style.css
eval/             settings-router eval set (189 queries) + runner
tests/            synthetic-fixture tests: extra sources, scanned pages, previews, time-aware search,
                  folder watcher (test_fresh: new, renamed, moved, deleted, partial downloads, rapid saves),
                  key masking + .env (test_secrets), search this folder (test_scope)
models/           EmbeddingGemma 2 .litertlm + GPU kernel cache
```
