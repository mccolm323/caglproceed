# California Grand Lodge Proceedings — Search

A searchable website over the Grand Lodge of California's Proceedings
archive (1850–present) — keyword search (SQLite FTS5) over OCR'd text,
each result linking to the source PDF at the correct page.

## How it fits together

- `source-text/*.txt` — extracted/OCR'd page text, committed to this repo.
- `scripts/build_db.py` — rebuilds the FTS5 search database from
  `source-text/` on every Render deploy (the database itself is never
  committed).
- PDFs (13GB) are hosted on Cloudflare R2, not in this repo. Search
  results link to `https://<r2-domain>/<file>.pdf#page=N`.
- `webapp.py` — the Render web service; stdlib-only Python.

## Local development

```bash
python3 scripts/build_db.py
PDF_BASE_URL=https://pub-xxxxxxxxxxxx.r2.dev python3 webapp.py 8765
```

Then open http://127.0.0.1:8765.

## Running tests

```bash
(cd scripts/lib && python3 -m unittest db_builder_test upload_plan_test -v)
(cd scripts && python3 -m unittest build_db_test -v)
python3 -m unittest webapp_test -v
```

## Uploading PDFs to Cloudflare R2 (one-time, or when volumes are added)

1. Create a free Cloudflare account and an R2 bucket with public read
   access enabled.
2. Create an R2 API token (S3-compatible credentials).
3. Copy `.env.example` to `.env` and fill in the R2 values. **Never
   commit `.env`.**
4. `python3 -m venv venv && source venv/bin/activate && pip install -r requirements-upload.txt`
5. `python3 scripts/upload_pdfs.py --dry-run` to preview the upload plan.
6. `python3 scripts/upload_pdfs.py` to actually upload (safe to
   interrupt/re-run — skips files already in the bucket).

## Deploying

1. Push this repo to GitHub.
2. In Render, choose **New > Blueprint** (not "Web Service" — only the
   Blueprint flow reads `render.yaml` automatically) and connect the
   repo. Render will read `render.yaml` and set up the build command
   (`python3 scripts/build_db.py`), start command (`python3 webapp.py`),
   and the **free** plan for you (pinned via `plan: free` in
   `render.yaml`, so Render won't default you into a paid plan).
3. Render's blueprint prompts for the `PDF_BASE_URL` env var (marked
   `sync: false` in `render.yaml`) — set it to your R2 bucket's public
   URL. You can also set/change it later from the service's Environment
   tab in Render's dashboard.
4. Deploy. Render redeploys automatically on every push, rebuilding the
   search index from `source-text/` each time.

Note: the free plan sleeps after 15 minutes of inactivity and takes
~30-60s to wake up on the next visit. Upgrade to Starter (~$7/mo) later
if you want it always-on — just remove or change `plan: free` in
`render.yaml` and push.

## Adding new volumes later

1. OCR/extract the new volume's text into `source-text/<name>.txt`
   (form-feed `\x0c` between pages), add its entry to
   `source-text/mapping.json`.
2. Upload the new PDF: `python3 scripts/upload_pdfs.py`.
3. Commit and push `source-text/` — Render rebuilds the index on deploy.
