# CA GL Proceedings Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a keyword-searchable website over the California Grand Lodge Proceedings (129+ volumes, ~49,754 pages), deployed as a Render Web Service with source on GitHub and PDFs hosted on Cloudflare R2.

**Architecture:** A Python stdlib-only web service (`webapp.py`, adapted from the user's existing local version) serves search results from a SQLite FTS5 database that Render rebuilds from committed plain-text sources (`source-text/*.txt`) on every deploy. PDFs never enter git — they're uploaded once to Cloudflare R2 via a separate local script, and search results link to `https://<r2-domain>/<file>.pdf#page=N`.

**Tech Stack:** Python 3 (stdlib: `sqlite3`, `http.server`), boto3 (local upload script only), SQLite FTS5, Cloudflare R2, Render.

**Spec:** [docs/superpowers/specs/2026-09-27-ca-gl-proceedings-search-design.md](../specs/2026-09-27-ca-gl-proceedings-search-design.md)

## Global Constraints

- `webapp.py` and `scripts/build_db.py` (anything Render runs) must stay stdlib-only — no pip dependencies to manage on Render.
- `boto3` is allowed only in `scripts/upload_pdfs.py` and `scripts/lib/upload_plan.py` (local one-time tool, never deployed).
- FTS5 schema is fixed: `volumes(id, label, source_txt, num_pages, num_chars)` and `pages_fts(content, volume_label UNINDEXED, volume_id UNINDEXED, page_num UNINDEXED, tokenize='porter unicode61')` — verified against the user's existing known-good database.
- `page_num` must be the 1-indexed position of a page among *all* form-feed-delimited segments in a volume's text (gaps preserved for empty segments), not a re-numbered sequence of only non-empty pages — this is what keeps `#page=N` links aligned with real PDF page numbers. Verified against the existing DB (e.g. `Proceedings 2020` has pages numbered 1–498 with gaps, 473 non-empty).
- PDFs and the compiled `.db` file are never committed to git.
- Secrets (R2 credentials) live only in a local, gitignored `.env` — never committed, never hardcoded.
- Render service must bind `0.0.0.0` and read the port from the `$PORT` env var.

## Review Focus

- Malformed FTS5 query syntax (e.g. unbalanced quotes) must return a JSON error, not crash the server.
- A non-integer `volume_id` query parameter must return a JSON error, not raise an uncaught `ValueError`.
- An empty/whitespace-only search query must return empty results without touching the database.
- A search result whose volume has no PDF mapping entry must render without a broken/exception-raising link.
- PDF filenames containing spaces and parentheses (common in this corpus) must be correctly percent-encoded in generated R2 URLs.

---

## Task 1: Repo scaffolding

**Files:**
- Create: `.gitignore`
- Create: `requirements-upload.txt`
- Create: `.env.example`

**Interfaces:**
- Produces: the ignore rules and env var names every later task assumes exist.

- [ ] **Step 1: Write `.gitignore`**

```gitignore
# Generated/rebuilt, never committed
*.db
*.db.gz
__pycache__/
*.pyc

# Secrets
.env

# Local venv for the upload script
venv/
```

- [ ] **Step 2: Write `requirements-upload.txt`**

```
boto3>=1.34
```

- [ ] **Step 3: Write `.env.example`**

```bash
# --- scripts/upload_pdfs.py (local one-time upload, needs write access) ---
R2_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=ca-gl-proceedings

# --- webapp.py (Render env var, public read URL prefix, NOT a secret) ---
PDF_BASE_URL=https://pub-xxxxxxxxxxxx.r2.dev
```

- [ ] **Step 4: Commit**

```bash
git add .gitignore requirements-upload.txt .env.example
git commit -m "chore: scaffold repo (gitignore, env template, upload deps)"
```

---

## Task 2: Commit extracted source text

**Files:**
- Create: `source-text/*.txt` (copied from `~/Documents/California GL Proceedings/_extracted_text/`)
- Create: `source-text/mapping.json` (copied from the same folder)

**Interfaces:**
- Produces: `source-text/` — the directory `scripts/build_db.py` (Task 4) reads.

- [ ] **Step 1: Copy the extracted text and mapping**

```bash
mkdir -p source-text
cp ~/Documents/California\ GL\ Proceedings/_extracted_text/*.txt source-text/
cp ~/Documents/California\ GL\ Proceedings/_extracted_text/mapping.json source-text/
```

- [ ] **Step 2: Verify the copy is complete**

```bash
ls source-text/*.txt | wc -l
```

Expected: `131` (matches the `.txt` file count in the source folder). If it doesn't match, list what's missing before continuing:

```bash
diff <(ls ~/Documents/California\ GL\ Proceedings/_extracted_text/*.txt | xargs -n1 basename | sort) \
     <(ls source-text/*.txt | xargs -n1 basename | sort)
```

- [ ] **Step 3: Commit**

```bash
git add source-text/
git commit -m "data: add extracted proceedings text (source for the search index)"
```

---

## Task 3: `db_builder` — pure indexing logic

**Files:**
- Create: `scripts/lib/db_builder.py`
- Test: `scripts/lib/db_builder_test.py`

**Interfaces:**
- Produces:
  - `parse_pages(text: str) -> list[str]`
  - `derive_label(txt_filename: str) -> str`
  - `year_of(label: str) -> int`
  - `create_schema(conn: sqlite3.Connection) -> None`
  - `insert_volume(conn: sqlite3.Connection, volume_id: int, txt_filename: str, text: str) -> tuple[int, int]` (returns `(num_pages, num_chars)`)
- Consumes: nothing (pure module, stdlib `sqlite3`/`re` only).

- [ ] **Step 1: Write the failing tests**

```python
# scripts/lib/db_builder_test.py
import sqlite3
import unittest

from db_builder import (
    create_schema,
    derive_label,
    insert_volume,
    parse_pages,
    year_of,
)


class TestParsePages(unittest.TestCase):
    def test_splits_on_form_feed(self):
        text = "page one\x0cpage two\x0cpage three"
        self.assertEqual(parse_pages(text), ["page one", "page two", "page three"])

    def test_strips_whitespace_per_page(self):
        text = "  page one  \x0c  page two  "
        self.assertEqual(parse_pages(text), ["page one", "page two"])

    def test_trailing_form_feed_yields_empty_last_page(self):
        text = "only page\x0c"
        self.assertEqual(parse_pages(text), ["only page", ""])

    def test_no_form_feed_is_single_page(self):
        self.assertEqual(parse_pages("just text"), ["just text"])


class TestDeriveLabel(unittest.TestCase):
    def test_replaces_underscores_and_strips_extension(self):
        self.assertEqual(
            derive_label("Proceedings_1910_VOL._XXX.txt"),
            "Proceedings 1910 VOL. XXX",
        )

    def test_handles_year_range_filenames(self):
        self.assertEqual(
            derive_label("Proceedings_1893-1894_VOL._XXI.txt"),
            "Proceedings 1893-1894 VOL. XXI",
        )

    def test_handles_simple_year_filenames(self):
        self.assertEqual(derive_label("Proceedings_2020.txt"), "Proceedings 2020")


class TestYearOf(unittest.TestCase):
    def test_extracts_19th_century_year(self):
        self.assertEqual(year_of("Proceedings 1893-1894 VOL. XXI"), 1893)

    def test_extracts_20th_century_year(self):
        self.assertEqual(year_of("Proceedings 1960 - Vol. LXIII"), 1960)

    def test_extracts_21st_century_year(self):
        self.assertEqual(year_of("Proceedings 2020"), 2020)

    def test_missing_year_sorts_last(self):
        self.assertEqual(year_of("No Year Here"), 9999)


class TestInsertVolume(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        create_schema(self.conn)

    def test_inserts_volume_row_and_non_empty_pages(self):
        text = "first page text\x0c\x0cthird page text"
        num_pages, num_chars = insert_volume(self.conn, 1, "Proceedings_2020.txt", text)

        self.assertEqual(num_pages, 2)
        self.assertEqual(num_chars, len("first page text") + len("third page text"))

        vol = self.conn.execute(
            "SELECT label, source_txt, num_pages FROM volumes WHERE id = 1"
        ).fetchone()
        self.assertEqual(vol, ("Proceedings 2020", "Proceedings_2020.txt", 2))

    def test_preserves_page_number_gaps_for_empty_pages(self):
        text = "one\x0c\x0cthree"
        insert_volume(self.conn, 1, "Proceedings_2020.txt", text)
        page_nums = [
            row[0]
            for row in self.conn.execute(
                "SELECT page_num FROM pages_fts WHERE volume_id = 1 ORDER BY page_num"
            )
        ]
        self.assertEqual(page_nums, [1, 3])

    def test_content_is_searchable_via_fts(self):
        insert_volume(self.conn, 1, "Proceedings_2020.txt", "Earl Warren spoke today")
        rows = self.conn.execute(
            "SELECT page_num FROM pages_fts WHERE pages_fts MATCH 'warren'"
        ).fetchall()
        self.assertEqual(rows, [(1,)])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd scripts/lib && python3 -m unittest db_builder_test -v
```

Expected: FAIL/ERROR — `db_builder` module doesn't exist yet.

- [ ] **Step 3: Write the implementation**

```python
# scripts/lib/db_builder.py
"""Pure logic for turning extracted proceedings text into the FTS5 search DB.

No I/O beyond the sqlite3 connection passed in — everything here is
unit-testable with an in-memory database.
"""
import re
import sqlite3

FORM_FEED = "\x0c"

SCHEMA_SQL = """
CREATE TABLE volumes (
    id INTEGER PRIMARY KEY,
    label TEXT,
    source_txt TEXT,
    num_pages INTEGER,
    num_chars INTEGER
);
CREATE INDEX idx_volumes_label ON volumes(label);
CREATE VIRTUAL TABLE pages_fts USING fts5(
    content,
    volume_label UNINDEXED,
    volume_id UNINDEXED,
    page_num UNINDEXED,
    tokenize='porter unicode61'
);
"""

YEAR_RE = re.compile(r"(1[89]\d{2}|20\d{2})")


def parse_pages(text):
    """Split a volume's extracted text into pages on the form-feed delimiter."""
    return [page.strip() for page in text.split(FORM_FEED)]


def derive_label(txt_filename):
    """'Proceedings_1910_VOL._XXX.txt' -> 'Proceedings 1910 VOL. XXX'."""
    name = txt_filename
    if name.endswith(".txt"):
        name = name[: -len(".txt")]
    return " ".join(name.replace("_", " ").split())


def year_of(label):
    m = YEAR_RE.search(label)
    return int(m.group(1)) if m else 9999


def create_schema(conn):
    conn.executescript(SCHEMA_SQL)


def insert_volume(conn, volume_id, txt_filename, text):
    """Insert one volume row plus a pages_fts row per non-empty page.

    Returns (num_pages, num_chars) for the volume.
    """
    label = derive_label(txt_filename)
    pages = parse_pages(text)
    non_empty = [(i, p) for i, p in enumerate(pages, start=1) if p]
    num_chars = sum(len(p) for _, p in non_empty)

    conn.execute(
        "INSERT INTO volumes (id, label, source_txt, num_pages, num_chars) "
        "VALUES (?, ?, ?, ?, ?)",
        (volume_id, label, txt_filename, len(non_empty), num_chars),
    )
    conn.executemany(
        "INSERT INTO pages_fts (content, volume_label, volume_id, page_num) "
        "VALUES (?, ?, ?, ?)",
        [(text, label, volume_id, page_num) for page_num, text in non_empty],
    )
    return len(non_empty), num_chars
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd scripts/lib && python3 -m unittest db_builder_test -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/db_builder.py scripts/lib/db_builder_test.py
git commit -m "feat: add pure FTS5 indexing logic for proceedings text"
```

---

## Task 4: `build_db.py` — build command entry point

**Files:**
- Create: `scripts/build_db.py`
- Test: `scripts/build_db_test.py`

**Interfaces:**
- Consumes: `db_builder.create_schema`, `db_builder.insert_volume` (Task 3).
- Produces: `build(source_dir: str, out_path: str) -> dict` with keys `volumes`, `pages`, `skipped` (list of `(filename, error_message)`); a `main()` CLI wired to this, run as `python3 scripts/build_db.py`.

- [ ] **Step 1: Write the failing tests**

```python
# scripts/build_db_test.py
import os
import sqlite3
import tempfile
import unittest

from build_db import build


class TestBuild(unittest.TestCase):
    def test_builds_db_from_txt_files(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "Proceedings_2020.txt"), "w") as f:
                f.write("Earl Warren spoke\x0cA second page")
            with open(os.path.join(d, "Proceedings_2021.txt"), "w") as f:
                f.write("Another volume entirely")

            out_db = os.path.join(d, "out.db")
            result = build(d, out_db)

            self.assertEqual(result["volumes"], 2)
            self.assertEqual(result["pages"], 3)
            self.assertEqual(result["skipped"], [])

            conn = sqlite3.connect(out_db)
            count = conn.execute("SELECT COUNT(*) FROM volumes").fetchone()[0]
            self.assertEqual(count, 2)

    def test_skips_unreadable_file_and_continues(self):
        with tempfile.TemporaryDirectory() as d:
            good = os.path.join(d, "Good.txt")
            bad = os.path.join(d, "Bad.txt")
            with open(good, "w") as f:
                f.write("hello world")
            with open(bad, "w") as f:
                f.write("unreadable")
            os.chmod(bad, 0o000)
            try:
                result = build(d, os.path.join(d, "out.db"))
            finally:
                os.chmod(bad, 0o644)

            self.assertEqual(result["volumes"], 1)
            self.assertEqual(len(result["skipped"]), 1)
            self.assertEqual(result["skipped"][0][0], "Bad.txt")

    def test_overwrites_an_existing_db_file(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "A.txt"), "w") as f:
                f.write("first build")
            out_db = os.path.join(d, "out.db")
            build(d, out_db)
            first_size = os.path.getsize(out_db)
            self.assertGreater(first_size, 0)

            # Rebuild from scratch should not error on an already-existing file.
            result = build(d, out_db)
            self.assertEqual(result["volumes"], 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd scripts && python3 -m unittest build_db_test -v
```

Expected: FAIL/ERROR — `build_db` module doesn't exist yet.

- [ ] **Step 3: Write the implementation**

```python
#!/usr/bin/env python3
# scripts/build_db.py
"""Rebuild the proceedings FTS5 search database from source-text/*.txt.

Runs as Render's build command on every deploy, and locally for testing:

    python3 scripts/build_db.py [--source-dir DIR] [--out DB_PATH]
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from db_builder import create_schema, insert_volume  # noqa: E402


def build(source_dir, out_path):
    txt_files = sorted(f for f in os.listdir(source_dir) if f.endswith(".txt"))

    if os.path.exists(out_path):
        os.remove(out_path)
    conn = sqlite3.connect(out_path)
    create_schema(conn)

    total_pages = 0
    skipped = []
    volume_id = 0
    for txt_filename in txt_files:
        path = os.path.join(source_dir, txt_filename)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as e:
            skipped.append((txt_filename, str(e)))
            continue
        volume_id += 1
        num_pages, _ = insert_volume(conn, volume_id, txt_filename, text)
        total_pages += num_pages

    conn.commit()
    conn.close()
    return {"volumes": volume_id, "pages": total_pages, "skipped": skipped}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", default="source-text")
    parser.add_argument("--out", default="CA_GL_Proceedings_FullText.db")
    args = parser.parse_args()

    print(f"Building DB from {args.source_dir} -> {args.out}")
    result = build(args.source_dir, args.out)
    print(f"Volumes: {result['volumes']}, pages: {result['pages']}")
    if result["skipped"]:
        print(f"Skipped {len(result['skipped'])} file(s):")
        for name, err in result["skipped"]:
            print(f"  {name}: {err}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd scripts && python3 -m unittest build_db_test -v
```

Expected: all tests PASS.

- [ ] **Step 5: Regression check against the real corpus**

```bash
cd /Users/admin/Documents/ca-gl-proceedings-search
python3 scripts/build_db.py --source-dir source-text --out /tmp/regression-check.db
```

Expected output: `Volumes: 131, pages:` a number close to `49754` (the known-good baseline from the user's existing database — some variance is expected since `source-text/` may include a couple of files added since that baseline was built). Investigate before continuing if pages is off by more than a few hundred, or if `skipped` reports more than 0-1 files.

- [ ] **Step 6: Commit**

```bash
rm -f /tmp/regression-check.db
git add scripts/build_db.py scripts/build_db_test.py
git commit -m "feat: add build_db.py CLI to rebuild the search index from source text"
```

---

## Task 5: `upload_plan` — pure PDF upload-set logic

**Files:**
- Create: `scripts/lib/upload_plan.py`
- Test: `scripts/lib/upload_plan_test.py`

**Interfaces:**
- Produces:
  - `is_duplicate_copy(filename: str, all_filenames: set[str]) -> bool`
  - `plan_uploads(local_filenames: list[str], already_uploaded: set[str]) -> list[str]`
- Consumes: nothing (pure module, no network, no boto3).

- [ ] **Step 1: Write the failing tests**

```python
# scripts/lib/upload_plan_test.py
import unittest

from upload_plan import is_duplicate_copy, plan_uploads


class TestIsDuplicateCopy(unittest.TestCase):
    def test_detects_numbered_suffix_duplicate(self):
        names = {"Proceedings_1850-1854_VOL._I.pdf", "Proceedings_1850-1854_VOL._I (1).pdf"}
        self.assertTrue(is_duplicate_copy("Proceedings_1850-1854_VOL._I (1).pdf", names))

    def test_does_not_flag_the_original(self):
        names = {"Proceedings_1850-1854_VOL._I.pdf", "Proceedings_1850-1854_VOL._I (1).pdf"}
        self.assertFalse(is_duplicate_copy("Proceedings_1850-1854_VOL._I.pdf", names))

    def test_does_not_flag_an_unrelated_file(self):
        names = {"Proceedings_2020.pdf"}
        self.assertFalse(is_duplicate_copy("Proceedings_2020.pdf", names))

    def test_numbered_suffix_without_a_matching_base_is_not_a_duplicate(self):
        names = {"Something (1).pdf"}
        self.assertFalse(is_duplicate_copy("Something (1).pdf", names))


class TestPlanUploads(unittest.TestCase):
    def test_excludes_duplicate_copies(self):
        local = ["A.pdf", "A (1).pdf", "B.pdf"]
        self.assertEqual(plan_uploads(local, already_uploaded=set()), ["A.pdf", "B.pdf"])

    def test_excludes_already_uploaded_files(self):
        local = ["A.pdf", "B.pdf"]
        self.assertEqual(plan_uploads(local, already_uploaded={"A.pdf"}), ["B.pdf"])

    def test_empty_local_list(self):
        self.assertEqual(plan_uploads([], already_uploaded=set()), [])

    def test_result_is_sorted(self):
        local = ["Zebra.pdf", "Apple.pdf"]
        self.assertEqual(plan_uploads(local, already_uploaded=set()), ["Apple.pdf", "Zebra.pdf"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd scripts/lib && python3 -m unittest upload_plan_test -v
```

Expected: FAIL/ERROR — `upload_plan` module doesn't exist yet.

- [ ] **Step 3: Write the implementation**

```python
# scripts/lib/upload_plan.py
"""Pure logic for deciding which local PDFs to upload to R2.

No network calls here — this module only computes plans from filename
lists the caller supplies, so it's fully unit-testable.
"""
import re

DUPLICATE_SUFFIX_RE = re.compile(r"^(?P<base>.+) \(\d+\)\.pdf$", re.IGNORECASE)


def is_duplicate_copy(filename, all_filenames):
    """True if `filename` is a ' (N)'-style duplicate of a file also present."""
    m = DUPLICATE_SUFFIX_RE.match(filename)
    if not m:
        return False
    base = f"{m.group('base')}.pdf"
    return base in all_filenames


def plan_uploads(local_filenames, already_uploaded):
    """Filenames that still need uploading: excludes duplicate copies and
    anything already present in the remote bucket."""
    all_names = set(local_filenames)
    to_upload = [
        name
        for name in local_filenames
        if not is_duplicate_copy(name, all_names) and name not in already_uploaded
    ]
    return sorted(to_upload)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd scripts/lib && python3 -m unittest upload_plan_test -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/upload_plan.py scripts/lib/upload_plan_test.py
git commit -m "feat: add pure upload-planning logic (dedup + resume support)"
```

---

## Task 6: `upload_pdfs.py` — one-time R2 upload script

**Files:**
- Create: `scripts/upload_pdfs.py`

**Interfaces:**
- Consumes: `upload_plan.plan_uploads` (Task 5); env vars `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME` (Task 1's `.env.example`).
- Produces: uploaded objects in the R2 bucket, keyed by PDF filename.

- [ ] **Step 1: Write the implementation**

```python
#!/usr/bin/env python3
# scripts/upload_pdfs.py
"""One-time upload of the proceedings PDFs to Cloudflare R2.

Skips ' (N)'-suffix duplicate files and anything already in the bucket,
so it's safe to interrupt (Ctrl-C) and re-run.

Requires boto3 (pip install -r requirements-upload.txt) and R2 credentials
in a local .env file (see .env.example) — never commit that file.

Usage:
    python3 scripts/upload_pdfs.py [--source-dir DIR] [--dry-run]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from upload_plan import plan_uploads  # noqa: E402

DEFAULT_SOURCE_DIR = os.path.expanduser("~/Documents/California GL Proceedings")


def load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def list_local_pdfs(source_dir):
    return sorted(f for f in os.listdir(source_dir) if f.lower().endswith(".pdf"))


def list_remote_keys(client, bucket):
    keys = set()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            keys.add(obj["Key"])
    return keys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--dry-run", action="store_true", help="print the plan, upload nothing")
    args = parser.parse_args()

    load_dotenv()
    bucket = os.environ.get("R2_BUCKET_NAME")
    if not bucket:
        sys.exit("R2_BUCKET_NAME is not set (check .env against .env.example)")

    local_files = list_local_pdfs(args.source_dir)
    print(f"Found {len(local_files)} PDF(s) in {args.source_dir}")

    if args.dry_run:
        to_upload = plan_uploads(local_files, already_uploaded=set())
        print(f"Would upload {len(to_upload)} file(s) (dedup only, remote state not checked):")
        for name in to_upload:
            print(f"  {name}")
        return

    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    )

    already_uploaded = list_remote_keys(client, bucket)
    to_upload = plan_uploads(local_files, already_uploaded)
    print(f"{len(already_uploaded)} already in bucket, {len(to_upload)} to upload")

    for i, name in enumerate(to_upload, start=1):
        path = os.path.join(args.source_dir, name)
        size_mb = os.path.getsize(path) / (1024 * 1024)
        print(f"[{i}/{len(to_upload)}] Uploading {name} ({size_mb:.1f} MB)...")
        client.upload_file(path, bucket, name)

    print("Done.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Dry-run against the real directory to verify the dedup plan**

```bash
python3 scripts/upload_pdfs.py --dry-run
```

Expected: `Found 142 PDF(s)` and `Would upload 131 file(s)` — confirms the 11 known duplicates are correctly excluded. Investigate before continuing if the count differs.

- [ ] **Step 3: Commit**

```bash
git add scripts/upload_pdfs.py
git commit -m "feat: add one-time R2 upload script for proceedings PDFs"
```

- [ ] **Step 4: Run for real once R2 credentials are configured (manual, not part of this automated pass)**

After the user has created a Cloudflare account, an R2 bucket, and an API token, and filled in a local `.env` (copied from `.env.example`, never committed):

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements-upload.txt
python3 scripts/upload_pdfs.py
```

This takes a while (13GB). It's safe to interrupt and re-run — already-uploaded files are skipped.

---

## Task 7: `webapp.py` — Render web service

**Files:**
- Create: `webapp.py`
- Test: `webapp_test.py`

**Interfaces:**
- Consumes: `CA_GL_Proceedings_FullText.db` (built by Task 4's `build_db.py`, present in the same directory when this runs); `source-text/mapping.json` (Task 2); env vars `PORT` (Render-provided), `PDF_BASE_URL` (Task 1's `.env.example`).
- Produces: `pdf_url(base_url: str, filename: str, page: int) -> str`; an HTTP server exposing `GET /`, `GET /api/volumes`, `GET /api/search`.

- [ ] **Step 1: Write the failing tests**

```python
# webapp_test.py
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import webapp


class TestPdfUrl(unittest.TestCase):
    def test_builds_url_with_page_fragment(self):
        url = webapp.pdf_url("https://pub-xxxx.r2.dev", "Proceedings_2020.pdf", 42)
        self.assertEqual(url, "https://pub-xxxx.r2.dev/Proceedings_2020.pdf#page=42")

    def test_strips_trailing_slash_on_base_url(self):
        url = webapp.pdf_url("https://pub-xxxx.r2.dev/", "A.pdf", 1)
        self.assertEqual(url, "https://pub-xxxx.r2.dev/A.pdf#page=1")

    def test_percent_encodes_spaces_and_parens(self):
        url = webapp.pdf_url("https://pub-xxxx.r2.dev", "Proceedings (1901-1902).pdf", 5)
        self.assertEqual(
            url,
            "https://pub-xxxx.r2.dev/Proceedings%20%281901-1902%29.pdf#page=5",
        )


class TestServer(unittest.TestCase):
    """Integration test: boots the real handler against a tiny fixture DB."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        db_path = os.path.join(cls.tmpdir.name, "test.db")
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE volumes (
                id INTEGER PRIMARY KEY, label TEXT, source_txt TEXT,
                num_pages INTEGER, num_chars INTEGER
            );
            CREATE VIRTUAL TABLE pages_fts USING fts5(
                content, volume_label UNINDEXED, volume_id UNINDEXED,
                page_num UNINDEXED, tokenize='porter unicode61'
            );
            """
        )
        conn.execute(
            "INSERT INTO volumes VALUES (1, 'Proceedings 2020', 'Proceedings_2020.txt', 1, 20)"
        )
        conn.execute(
            "INSERT INTO pages_fts (content, volume_label, volume_id, page_num) "
            "VALUES ('Earl Warren spoke today', 'Proceedings 2020', 1, 7)"
        )
        # Volume 2 has no entry in mapping.json below, simulating a volume
        # whose PDF hasn't been mapped/uploaded yet.
        conn.execute(
            "INSERT INTO volumes VALUES (2, 'Proceedings 1875', 'Proceedings_1875.txt', 1, 20)"
        )
        conn.execute(
            "INSERT INTO pages_fts (content, volume_label, volume_id, page_num) "
            "VALUES ('the gavel was passed', 'Proceedings 1875', 2, 3)"
        )
        conn.commit()
        conn.close()

        mapping_path = os.path.join(cls.tmpdir.name, "mapping.json")
        with open(mapping_path, "w") as f:
            json.dump({"Proceedings_2020.pdf": "Proceedings_2020.txt"}, f)

        webapp.CONN = sqlite3.connect(db_path, check_same_thread=False)
        webapp.PDF_MAP = webapp.load_pdf_map(mapping_path)
        webapp.PDF_BASE_URL = "https://pub-xxxx.r2.dev"
        webapp.build_caches()

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), webapp.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.tmpdir.cleanup()

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as resp:
            return resp.status, json.loads(resp.read())

    def test_volumes_endpoint_lists_the_fixture_volume(self):
        status, data = self._get("/api/volumes")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["volumes"]), 1)
        self.assertEqual(data["volumes"][0]["label"], "Proceedings 2020")

    def test_search_finds_matching_page_with_pdf_link(self):
        status, data = self._get("/api/search?q=warren")
        self.assertEqual(status, 200)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["results"][0]["pdf"], "Proceedings_2020.pdf")
        self.assertEqual(data["results"][0]["page_num"], 7)

    def test_empty_query_returns_empty_results_without_error(self):
        status, data = self._get("/api/search?q=")
        self.assertEqual(status, 200)
        self.assertEqual(data, {"results": [], "total": 0})

    def test_malformed_fts_query_returns_json_error_not_crash(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get('/api/search?q=%22unbalanced')
        self.assertEqual(ctx.exception.code, 400)
        body = json.loads(ctx.exception.read())
        self.assertIn("error", body)

    def test_non_integer_volume_id_returns_json_error_not_crash(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/search?q=warren&volume_id=not-a-number")
        self.assertEqual(ctx.exception.code, 400)
        body = json.loads(ctx.exception.read())
        self.assertIn("error", body)

    def test_result_with_no_pdf_mapping_renders_without_a_link(self):
        status, data = self._get("/api/search?q=gavel")
        self.assertEqual(status, 200)
        self.assertEqual(data["total"], 1)
        result = data["results"][0]
        self.assertIsNone(result["pdf"])
        self.assertIsNone(result["pdf_url"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m unittest webapp_test -v
```

Expected: FAIL/ERROR — `webapp` module doesn't exist yet.

- [ ] **Step 3: Write the implementation**

```python
#!/usr/bin/env python3
# webapp.py
"""California Grand Lodge Proceedings - Render web service.

Serves search results from CA_GL_Proceedings_FullText.db (built by
scripts/build_db.py as Render's build step) and links each result to its
source PDF hosted on Cloudflare R2.

Local run:
    PDF_BASE_URL=https://pub-xxxx.r2.dev python3 webapp.py [port]
"""
import json
import os
import re
import sqlite3
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "CA_GL_Proceedings_FullText.db")
MAPPING_PATH = os.path.join(HERE, "source-text", "mapping.json")

YEAR_RE = re.compile(r"(1[89]\d{2}|20\d{2})")


def year_of(label):
    m = YEAR_RE.search(label)
    return int(m.group(1)) if m else 9999


def pdf_url(base_url, filename, page):
    return f"{base_url.rstrip('/')}/{urllib.parse.quote(filename)}#page={page}"


def load_pdf_map(mapping_path):
    """txt filename -> original pdf filename, for linking out to the source PDF."""
    m = {}
    if os.path.exists(mapping_path):
        with open(mapping_path) as f:
            raw = json.load(f)
        for pdf, txt in raw.items():
            m[txt] = pdf
    return m


CONN = None
PDF_MAP = {}
PDF_BASE_URL = ""
VOLUMES = []


def build_caches():
    global VOLUMES
    cur = CONN.execute("SELECT id, label, source_txt, num_pages, num_chars FROM volumes")
    vols = []
    for vid, label, src, pages, chars in cur.fetchall():
        vols.append(
            {
                "id": vid,
                "label": label,
                "source_txt": src,
                "num_pages": pages,
                "num_chars": chars,
                "year": year_of(label),
                "pdf": PDF_MAP.get(src),
            }
        )
    vols.sort(key=lambda v: (v["year"], v["label"]))
    VOLUMES = vols


INDEX_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>California Grand Lodge Proceedings — Search</title>
<style>
  :root {
    --bg: #faf7f2; --ink: #2b2620; --accent: #8a2c2c;
    --accent-light: #c8a24a; --card: #ffffff; --border: #e4ddd0; --muted: #6b6255;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: Georgia, 'Times New Roman', serif; background: var(--bg); color: var(--ink); }
  header {
    background: linear-gradient(180deg, #3a1f1f, #5a2a2a); color: #f4ead2;
    padding: 28px 20px 22px; text-align: center; border-bottom: 4px solid var(--accent-light);
  }
  header h1 { margin: 0 0 4px; font-size: 1.5rem; letter-spacing: 0.02em; }
  header p { margin: 0; font-size: 0.9rem; color: #d9c9a3; font-family: Arial, sans-serif; }
  .wrap { max-width: 880px; margin: 0 auto; padding: 20px 16px 60px; }
  .searchbar {
    display: flex; gap: 8px; flex-wrap: wrap; background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; padding: 14px; margin-top: -18px; box-shadow: 0 6px 18px rgba(0,0,0,0.08);
    font-family: Arial, sans-serif;
  }
  .searchbar input[type=text] { flex: 1 1 260px; padding: 10px 12px; font-size: 1rem; border: 1px solid var(--border); border-radius: 6px; }
  .searchbar select { padding: 10px; border-radius: 6px; border: 1px solid var(--border); font-size: 0.9rem; max-width: 280px; }
  .searchbar button { padding: 10px 18px; background: var(--accent); color: white; border: none; border-radius: 6px; font-size: 0.95rem; cursor: pointer; }
  .searchbar button:hover { background: #6f2323; }
  .meta { font-family: Arial, sans-serif; font-size: 0.85rem; color: var(--muted); margin: 14px 2px 6px; }
  .hint { font-family: Arial, sans-serif; font-size: 0.78rem; color: var(--muted); margin: 6px 2px 0; }
  .result { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; margin-bottom: 10px; }
  .result h3 { margin: 0 0 4px; font-size: 1rem; }
  .result h3 a { color: var(--accent); text-decoration: none; }
  .result h3 a:hover { text-decoration: underline; }
  .result .filepath { font-family: Arial, sans-serif; font-size: 0.75rem; color: var(--muted); margin-bottom: 6px; }
  .result .snippet { font-size: 0.98rem; line-height: 1.5; color: #3a342b; }
  .result mark { background: #f3d98b; padding: 0 2px; border-radius: 2px; }
  .loadmore { display: block; margin: 18px auto; padding: 10px 22px; font-family: Arial, sans-serif; background: white; border: 1px solid var(--border); border-radius: 6px; cursor: pointer; }
  .empty, .error { font-family: Arial, sans-serif; color: var(--muted); text-align: center; padding: 40px 0; }
  .error { color: #8a2c2c; }
  footer { text-align: center; font-family: Arial, sans-serif; font-size: 0.78rem; color: var(--muted); padding: 30px 0 10px; }
</style>
</head>
<body>
<header>
  <h1>Grand Lodge of California — Proceedings Archive</h1>
  <p>Full-text search across __VOLUME_COUNT__ volumes, __YEAR_RANGE__</p>
</header>
<div class="wrap">
  <div class="searchbar">
    <input type="text" id="q" placeholder='Search names, places, resolutions… e.g. "Earl Warren"' autofocus>
    <select id="volume"><option value="">All volumes</option></select>
    <button id="go">Search</button>
  </div>
  <div class="hint">Tip: use "quotes" for an exact phrase, OR / NOT for logic. Text comes from OCR, so very old volumes may have scanning errors.</div>
  <div class="meta" id="meta"></div>
  <div id="results"></div>
  <button class="loadmore" id="loadmore" style="display:none;">Load more results</button>
</div>
<footer>California Grand Lodge Proceedings Search</footer>
<script>
let state = { limit: 25, offset: 0, total: 0 };

async function loadVolumes() {
  const res = await fetch('/api/volumes');
  const data = await res.json();
  const sel = document.getElementById('volume');
  data.volumes.forEach(v => {
    const opt = document.createElement('option');
    opt.value = v.id;
    opt.textContent = `${v.label} (${v.num_pages} pp.)`;
    sel.appendChild(opt);
  });
}

function renderResults(results, append) {
  const box = document.getElementById('results');
  if (!append) box.innerHTML = '';
  if (results.length === 0 && !append) {
    box.innerHTML = '<div class="empty">No matches found.</div>';
    return;
  }
  for (const r of results) {
    const div = document.createElement('div');
    div.className = 'result';
    const title = r.pdf_url
      ? `<a href="${r.pdf_url}" target="_blank">${r.volume_label} — p.${r.page_num}</a>`
      : `${r.volume_label} — p.${r.page_num}`;
    const path = r.pdf ? `${r.pdf} — page ${r.page_num}` : '(source PDF not identified)';
    div.innerHTML = `<h3>${title}</h3><div class="filepath">${path}</div><div class="snippet">${r.snippet}</div>`;
    box.appendChild(div);
  }
}

async function runSearch(append) {
  const q = document.getElementById('q').value.trim();
  const volume_id = document.getElementById('volume').value;
  if (!q) return;
  if (!append) state.offset = 0;

  const params = new URLSearchParams({ q, volume_id, limit: state.limit, offset: state.offset });
  document.getElementById('meta').textContent = 'Searching…';
  let data;
  try {
    const res = await fetch('/api/search?' + params.toString());
    data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Search failed');
  } catch (e) {
    document.getElementById('results').innerHTML = `<div class="error">Search error: ${e.message}. Check your query syntax (unbalanced quotes, etc).</div>`;
    document.getElementById('meta').textContent = '';
    document.getElementById('loadmore').style.display = 'none';
    return;
  }

  state.total = data.total;
  renderResults(data.results, append);
  const shown = state.offset + data.results.length;
  document.getElementById('meta').textContent = data.total
    ? `${data.total} match${data.total === 1 ? '' : 'es'} — showing ${shown}`
    : '';
  document.getElementById('loadmore').style.display = (shown < data.total) ? 'block' : 'none';
}

document.getElementById('go').addEventListener('click', () => runSearch(false));
document.getElementById('q').addEventListener('keydown', e => { if (e.key === 'Enter') runSearch(false); });
document.getElementById('loadmore').addEventListener('click', () => {
  state.offset += state.limit;
  runSearch(true);
});

loadVolumes();
</script>
</body>
</html>
"""


def render_index_html():
    years = [v["year"] for v in VOLUMES if v["year"] != 9999]
    year_range = f"{min(years)}–{max(years)}" if years else "?"
    return (
        INDEX_HTML_TEMPLATE.replace("__VOLUME_COUNT__", str(len(VOLUMES)))
        .replace("__YEAR_RANGE__", year_range)
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self._send_html(render_index_html())
            return

        if path == "/api/volumes":
            self._send_json({"volumes": VOLUMES})
            return

        if path == "/api/search":
            q = (qs.get("q") or [""])[0].strip()
            volume_id_raw = (qs.get("volume_id") or [""])[0].strip()
            limit = int((qs.get("limit") or ["25"])[0])
            offset = int((qs.get("offset") or ["0"])[0])
            if not q:
                self._send_json({"results": [], "total": 0})
                return

            volume_id = None
            if volume_id_raw:
                try:
                    volume_id = int(volume_id_raw)
                except ValueError:
                    self._send_json({"error": "volume_id must be an integer"}, status=400)
                    return

            try:
                sql = """
                    SELECT volume_label, volume_id, page_num,
                           snippet(pages_fts, 0, '<mark>', '</mark>', ' … ', 24)
                    FROM pages_fts
                    WHERE pages_fts MATCH ?
                """
                params = [q]
                count_sql = "SELECT COUNT(*) FROM pages_fts WHERE pages_fts MATCH ?"
                count_params = [q]
                if volume_id is not None:
                    sql += " AND volume_id = ?"
                    params.append(volume_id)
                    count_sql += " AND volume_id = ?"
                    count_params.append(volume_id)
                total = CONN.execute(count_sql, count_params).fetchone()[0]
                sql += " ORDER BY rank LIMIT ? OFFSET ?"
                params += [limit, offset]
                rows = CONN.execute(sql, params).fetchall()
            except sqlite3.OperationalError as e:
                self._send_json({"error": str(e)}, status=400)
                return

            vol_by_id = {v["id"]: v for v in VOLUMES}
            results = []
            for label, vid, page, snippet in rows:
                v = vol_by_id.get(vid, {})
                pdf = v.get("pdf")
                results.append(
                    {
                        "volume_label": label,
                        "volume_id": vid,
                        "page_num": page,
                        "snippet": snippet,
                        "pdf": pdf,
                        "pdf_url": pdf_url(PDF_BASE_URL, pdf, page) if pdf and PDF_BASE_URL else None,
                    }
                )
            self._send_json({"results": results, "total": total})
            return

        self.send_response(404)
        self.end_headers()


def main():
    global CONN, PDF_MAP, PDF_BASE_URL

    if not os.path.exists(DB):
        sys.exit(f"{DB} not found — run scripts/build_db.py first")

    PDF_BASE_URL = os.environ.get("PDF_BASE_URL", "")
    PDF_MAP = load_pdf_map(MAPPING_PATH)
    CONN = sqlite3.connect(DB, check_same_thread=False)
    build_caches()

    port = int(os.environ.get("PORT", sys.argv[1] if len(sys.argv) > 1 else 8765))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"California Grand Lodge Proceedings search running on port {port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m unittest webapp_test -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add webapp.py webapp_test.py
git commit -m "feat: add Render-ready webapp.py serving search over R2-hosted PDFs"
```

---

## Task 8: Deployment config, docs, and end-to-end local check

**Files:**
- Create: `render.yaml`
- Create: `README.md`

**Interfaces:**
- Consumes: everything from Tasks 1–7.

- [ ] **Step 1: Write `render.yaml`**

```yaml
services:
  - type: web
    name: ca-gl-proceedings-search
    env: python
    buildCommand: "python3 scripts/build_db.py"
    startCommand: "python3 webapp.py"
    envVars:
      - key: PDF_BASE_URL
        sync: false
```

- [ ] **Step 2: Write `README.md`**

```markdown
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
2. In Render, create a new **Web Service**, connect the repo. Render
   picks up `render.yaml` automatically (build command
   `python3 scripts/build_db.py`, start command `python3 webapp.py`).
3. Set the `PDF_BASE_URL` environment variable in Render's dashboard to
   your R2 bucket's public URL.
4. Deploy. Render redeploys automatically on every push, rebuilding the
   search index from `source-text/` each time.

## Adding new volumes later

1. OCR/extract the new volume's text into `source-text/<name>.txt`
   (form-feed `\x0c` between pages), add its entry to
   `source-text/mapping.json`.
2. Upload the new PDF: `python3 scripts/upload_pdfs.py`.
3. Commit and push `source-text/` — Render rebuilds the index on deploy.
```

- [ ] **Step 3: End-to-end local verification**

```bash
python3 scripts/build_db.py
PDF_BASE_URL=https://example-bucket.r2.dev python3 webapp.py 8765 &
sleep 1
curl -s "http://127.0.0.1:8765/api/search?q=warren&limit=1" | python3 -m json.tool
kill %1
```

Expected: JSON with at least one result whose `pdf_url` starts with
`https://example-bucket.r2.dev/` and ends with `#page=<N>`.

- [ ] **Step 4: Commit**

```bash
git add render.yaml README.md
git commit -m "docs: add deployment config and setup instructions"
```

- [ ] **Step 5: Manual next steps (outside this plan's automation)**

These require the user's own accounts/credentials and are not run as part
of this plan:
- Create the GitHub repo and push (`git remote add origin ...`, `git push -u origin main`).
- Create the Cloudflare R2 bucket + API token, fill in `.env`, run
  `scripts/upload_pdfs.py` for real (Task 6, Step 4).
- Create the Render Web Service connected to the GitHub repo, set
  `PDF_BASE_URL` in Render's dashboard.
