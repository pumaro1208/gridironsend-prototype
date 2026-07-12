#!/usr/bin/env python3
"""Merge a fresh scrape into players_clean.csv with a carry-forward guard:
a school's rows are replaced only if the new scrape returned a plausible
roster (>=20 players). Schools that failed (0 or tiny counts — e.g. sites
that 403 in CI) keep their previous season's rows instead of being wiped."""
import csv, sys
from collections import Counter

def load(p):
    with open(p, encoding="utf-8") as f:
        return list(csv.DictReader(f))

def main(base_path, new_path, min_rows=20):
    base, new = load(base_path), load(new_path)
    counts = Counter(r["school"] for r in new)
    good = {s for s, c in counts.items() if c >= min_rows}
    kept = [r for r in base if r["school"] not in good]
    carried = sorted({r["school"] for r in kept})
    merged = [r for r in new if r["school"] in good] + kept
    with open(base_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(new[0].keys()) if new else list(base[0].keys()))
        w.writeheader(); w.writerows(merged)
    print(f"merged: {len(good)} schools refreshed, {len(carried)} carried forward"
          f"{' (' + ', '.join(carried[:10]) + ('...' if len(carried) > 10 else '') + ')' if carried else ''}")
    print(f"total rows: {len(merged)}")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
