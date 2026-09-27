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
