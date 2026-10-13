"""Check notes_search's fuzzy matcher against real Spotlight (mdfind) on this Mac.

Writes one small file per typo variant into a visible folder, waits for Spotlight to index
it, queries each base word at several fNNN thresholds, and compares with
notes_search.text.fuzzy_word_match.

Spotlight additionally caps fuzzy candidate terms per query in an index-dependent way, so
the expected outcome is: no Spotlight match that our matcher rejects ("missed" = 0), and a
small number of matches we accept that Spotlight's cap dropped ("extra").

    python3 scripts/fuzzy_lab.py [folder]
"""

from __future__ import annotations

import itertools
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from notes_search.text import fuzzy_word_match

BASES = ["recipe", "chocolate", "cake", "lisbon"]
THRESHOLDS = [80, 66, 50, 33]


def variants(base: str) -> dict[str, str]:
    out: dict[str, str] = {}

    def add(v: str, kind: str) -> None:
        if v and v != base and v not in out:
            out[v] = kind

    n = len(base)
    for i in range(n):
        add(base[:i] + "q" + base[i + 1 :], f"sub@{i}")
        add(base[:i] + base[i + 1 :], f"del@{i}")
        add(base[:i] + "q" + base[i:], f"ins@{i}")
        if i < n - 1:
            add(base[:i] + base[i + 1] + base[i] + base[i + 2 :], f"swap@{i}")
    for i, j in itertools.combinations(range(n), 2):
        add(base[:i] + "q" + base[i + 1 : j] + "z" + base[j + 1 :], f"sub2@{i},{j}")
    for i, j, k in itertools.combinations(range(n), 3):
        v = list(base)
        v[i], v[j], v[k] = "q", "z", "k"
        add("".join(v), f"sub3@{i},{j},{k}")
    return out


def mdfind(folder: Path, query: str) -> set[str]:
    out = subprocess.run(["mdfind", "-onlyin", str(folder), query], capture_output=True, text=True)
    return {Path(p).stem for p in out.stdout.splitlines()}


def main() -> None:
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else "fuzzylab").resolve()
    if any(part.startswith(".") for part in folder.parts):
        sys.exit("Spotlight does not index hidden folders; choose a visible path.")
    folder.mkdir(parents=True, exist_ok=True)
    table: dict[str, tuple[str, str]] = {}
    for base in BASES:
        for v in variants(base):
            name = f"v{len(table):04d}"
            table[name] = (v, base)
            (folder / f"{name}.txt").write_text(f"lorem {v} ipsum\n")
    subprocess.run(["mdimport", str(folder)], capture_output=True)
    for _ in range(100):
        if len(mdfind(folder, 'kMDItemTextContent="lorem*"cwd')) >= len(table):
            break
        time.sleep(3)
    else:
        sys.exit("Spotlight did not finish indexing the lab folder.")

    print(f"{len(table)} variants indexed in {folder}\n")
    print("threshold  base        spotlight  ours  missed  extra")
    for t in THRESHOLDS:
        for base in BASES:
            hits = mdfind(folder, f'kMDItemTextContent="{base}*"cwdf{t:03d}')
            names = [n for n, (_, b) in table.items() if b == base]
            ours = {n for n in names if fuzzy_word_match(base, table[n][0], True, t)}
            spot = {n for n in names if n in hits}
            print(
                f"f{t:03d}       {base:10s}  {len(spot):9d}  {len(ours):4d}  "
                f"{len(spot - ours):6d}  {len(ours - spot):5d}"
            )


if __name__ == "__main__":
    main()
