# CA GL Proceedings Search — Design Spec

Date: 2026-09-27

## Summary

A searchable website over the California Grand Lodge Proceedings collection
(1850–2025): 142 PDFs on disk (131 unique after dedup), ~49,754 pages of
already-OCR'd text across 129 volumes. Keyword full-text search
(SQLite FTS5, porter stemming), each result linking to the source PDF at
the correct page. Source hosted on GitHub, deployed as a Render Web
Service. PDFs (13GB) hosted on Cloudflare R2, not committed to git.

Adapts the user's existing local tooling (`webapp.py`,
`search_proceedings.py`, `_extracted_text/*.txt`, `mapping.json`,
`CA_GL_Proceedings_FullText.db`) rather than rebuilding from scratch.

## Goals / success criteria

- A visitor can keyword/phrase search the full text of all volumes and
  get results instantly, with FTS5 snippet highlighting.
- A visitor can filter by volume.
- Each result links directly to the source PDF (hosted on R2), opened at
  the correct page.
- Deploys as a Render Web Service; pushing to GitHub triggers a redeploy.
- The search DB is never committed as a binary blob — it's rebuilt from
  committed source text on every Render deploy.
- Duplicate PDFs (same file saved twice) are excluded from upload.

## Non-goals

- Semantic / "search by meaning" search (keyword-only, per user decision
  — this corpus is mostly exact-match name/place/date lookups, and OCR
  noise on the oldest volumes would hurt embedding quality anyway).
- Re-OCRing or re-extracting text from the PDFs (already done, already
  validated — reuse `_extracted_text/*.txt` as-is).
- An embedded PDF.js viewer (relies on the browser's native PDF viewer
  via a `#page=N` link, same as the user's existing local webapp).
- Editing/correcting OCR errors in the source text.

## Corpus facts (verified)

- 142 PDF files on disk at `~/Documents/California GL Proceedings/`;
  11 are exact-duplicate re-downloads (same size, `(1)` suffix) — 131
  unique volumes to upload.
- 13GB total; individual files range 4MB–416MB. Many already exceed
  GitHub's 100MB hard per-file limit, ruling out committing PDFs to git
  under any circumstances (LFS included, given free-tier storage/cost).
- `_extracted_text/*.txt`: 131 files, 281MB total, largest ~4.1MB —
  every file comfortably under GitHub's per-file limit.
- Pages within each `.txt` are delimited by form-feed (`\x0c`)
  characters — already the extraction convention in use.
- `mapping.json`: txt filename → source PDF filename (127 entries;
  excludes duplicates already).
- Existing `CA_GL_Proceedings_FullText.db` (SQLite FTS5, 329MB): 129
  volumes, 49,754 pages indexed. Schema:
  - `volumes(id, label, source_txt, num_pages, num_chars)`
  - `pages_fts` (FTS5 virtual table: `content, volume_label UNINDEXED,
    volume_id UNINDEXED, page_num UNINDEXED`, `tokenize='porter
    unicode61'`)
- OCR quality is noisy on the oldest handwritten/scanned volumes —
  expected and out of scope to fix.

## Architecture

```
Cloudflare R2 (131 deduped PDFs, ~13GB, public read)
        ^
        | one-time upload script (local, run once from the source folder)
        | result links: https://<r2-domain>/<file>.pdf#page=N

GitHub repo
  source-text/*.txt          (committed, 281MB, from _extracted_text/)
  source-text/mapping.json   (committed)
  scripts/build_db.py        (rebuilds FTS5 DB from source-text/)
  scripts/upload_pdfs.py     (one-time R2 upload, not run by Render)
  webapp.py                  (Render web service, adapted from existing)
        |
        | git push
        v
Render Web Service
  build command: python3 scripts/build_db.py
    -> writes CA_GL_Proceedings_FullText.db into the running container
       (never committed; rebuilt fresh on every deploy)
  start command: python3 webapp.py
```

## Data model

Unchanged from the user's existing DB schema (see Corpus facts above) —
`build_db.py` reproduces it exactly so `webapp.py`'s existing SQL needs
no changes.

## Build pipeline (`scripts/build_db.py`)

1. Read `source-text/mapping.json` for the txt → pdf filename mapping.
2. For each `.txt` in `source-text/`: split on `\x0c` into pages, derive
   a human-readable volume label from the filename (same convention as
   the existing DB's `label` column).
3. Insert one `volumes` row per file and one `pages_fts` row per
   non-empty page.
4. Write the DB next to `webapp.py` so it picks it up unchanged.
5. Log and skip (don't fail the build on) any `.txt` with no mapping
   entry or any unreadable file.
6. Runnable both locally (for testing/verification) and as Render's
   build command.

## PDF hosting (Cloudflare R2)

- Free account, one bucket, public-read enabled (R2's public bucket URL
  or a custom domain).
- `scripts/upload_pdfs.py`: one-time script, run locally against the
  original `~/Documents/California GL Proceedings/` folder — uploads
  the 131 unique PDFs (skips the 11 known duplicates by name), skips
  files already present in the bucket (safe to re-run).
- Credentials (R2 API token) go in a local, gitignored `.env` — never
  committed, never typed into any third-party web form by the assistant.
- Cost: free up to 10GB, ~$0.05/month for the remaining ~3GB, zero
  egress fees.

## Backend (`webapp.py`, adapted from the existing local version)

- Keep it stdlib-only (`http.server`, `sqlite3`) — zero dependencies to
  manage on Render.
- Changes from the current local version:
  - Bind to `0.0.0.0` and read the port from Render's `$PORT` env var
    (instead of defaulting to `127.0.0.1:8765`).
  - Replace `file://<local path>` PDF links with
    `https://<r2-public-domain>/<pdf filename>#page=N`.
  - Remove the `ensure_db()` gzip-decompress step (build_db.py already
    produces the DB fresh at build time; no `.db.gz` shipped anywhere).
  - Remove the `webbrowser.open()` auto-launch (not meaningful on a
    server).
- Everything else (search endpoint, volume filter, FTS5 snippet
  highlighting, pagination) is kept as-is — it already works.

## Frontend

The existing embedded HTML/CSS/JS in `webapp.py` (search bar, volume
dropdown, snippet highlighting, load-more pagination) is kept as-is.
Only the PDF link construction changes (R2 URL instead of `file://`).

## Deployment

1. GitHub: new repo `ca-gl-proceedings-search` (name adjustable),
   `source-text/`, `scripts/`, `webapp.py` committed. PDFs and the
   compiled `.db` are never committed (`.gitignore`'d).
2. Cloudflare R2: user creates account + bucket + API token; assistant
   writes and runs `upload_pdfs.py` once against it.
3. Render: new Web Service connected to the GitHub repo. Build command
   `python3 scripts/build_db.py`, start command `python3 webapp.py`.
   Free tier for testing; Starter ($7/mo) recommended for an
   always-on public site (user's call).

## Error handling

- Build script: a `.txt` with no mapping entry, or that fails to read,
  is logged and skipped — not fatal to the whole build.
- Backend: unchanged from the existing implementation, which already
  returns a JSON error for malformed FTS5 queries rather than crashing.
- Upload script: skips files already present in the bucket, so it's
  safe to interrupt and re-run.

## Testing

- Rebuild the DB locally via `build_db.py` and diff volume/page counts
  against the known-good existing DB (129 volumes, 49,754 pages) as a
  regression check.
- Spot-check a handful of keyword queries against known content (e.g. a
  name known to appear in a specific volume/year).
- After R2 upload: confirm a sampled PDF URL loads and `#page=N`
  actually jumps to that page in-browser.
- After Render deploy: confirm the live search returns results and a
  result link opens the correct PDF at the correct page.

## Out of scope / future enhancements

- Semantic search, if OCR quality and user need later justify it.
- Deduplicating/cleaning OCR errors.
- An inline PDF.js viewer instead of the browser's native one.
- Migrating off SQLite FTS5 if the corpus grows enough to need it
  (not expected at this scale).
