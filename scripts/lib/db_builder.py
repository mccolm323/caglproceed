"""Pure logic for turning extracted proceedings text into the FTS5 search DB.

No I/O beyond the sqlite3 connection passed in — everything here is
unit-testable with an in-memory database.
"""
import re

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
        [(page_text, label, volume_id, page_num) for page_num, page_text in non_empty],
    )
    return len(non_empty), num_chars
