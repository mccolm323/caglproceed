#!/usr/bin/env python3
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
