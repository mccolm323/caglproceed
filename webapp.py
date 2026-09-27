#!/usr/bin/env python3
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
