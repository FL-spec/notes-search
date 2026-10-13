"""Compare notes-search's matching with real Spotlight on a synthetic corpus.

Notes runs its main query, ``field="*search*"cwdt``, through Spotlight. This script writes the
corpus in ``scripts/lab/corpus.json`` as plain text files, waits for Spotlight to index them,
runs every query in ``scripts/lab/queries.json`` through ``mdfind`` with that exact query, and
compares the files Spotlight returns with the notes notes-search matches.

    python3 scripts/matching_lab.py [folder]

macOS only. The folder must be visible: Spotlight doesn't index hidden folders.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from notes_search.engine import SearchEngine  # noqa: E402
from notes_search.query import escape_search_string  # noqa: E402
from notes_search.sources import from_folder  # noqa: E402

LAB = ROOT / "scripts" / "lab"


def mdfind(folder: Path, query: str) -> set[str]:
    out = subprocess.run(["mdfind", "-onlyin", str(folder), query],
                         capture_output=True, text=True, timeout=300)
    return {Path(p).stem for p in out.stdout.splitlines()}


def main() -> None:
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else "fuzzylab/corpus").resolve()
    if any(part.startswith(".") for part in folder.parts):
        sys.exit("Spotlight does not index hidden folders; choose a visible path.")
    folder.mkdir(parents=True, exist_ok=True)
    corpus = json.loads((LAB / "corpus.json").read_text(encoding="utf-8"))
    for i, note in enumerate(corpus):
        (folder / f"n{i:02d}.txt").write_text(f"{note['t']}\n{note['b']}\n", encoding="utf-8")
    subprocess.run(["mdimport", str(folder)], capture_output=True)
    # Spotlight indexes plain words first and compound tokens (emails, URLs, numbers with
    # separators) in a later pass. Querying before that pass finishes gives wrong answers, so
    # wait until a compound from the corpus is searchable, then give the index a moment more.
    print("Waiting for Spotlight to finish indexing (this can take a few minutes)…")
    for _ in range(200):
        files = mdfind(folder, 'kMDItemDisplayName = "n*"')
        if len(files) >= len(corpus) and mdfind(folder, 'kMDItemTextContent="*user@example.com*"cwdt') \
                and mdfind(folder, 'kMDItemTextContent="*3.14.159*"cwdt'):
            break
        time.sleep(5)
    else:
        sys.exit("Spotlight did not finish indexing the lab folder.")
    time.sleep(30)

    engine = SearchEngine(list(from_folder(folder)), natural_language=False)
    total = agreed = 0
    for name, queries in json.loads((LAB / "queries.json").read_text(encoding="utf-8")).items():
        spotlight_query = [f'kMDItemTextContent="*{escape_search_string(q)}*"cwdt' for q in queries]
        with ThreadPoolExecutor(8) as pool:
            spotlight = list(pool.map(lambda q: mdfind(folder, q), spotlight_query))
        diffs = []
        for query, expected in zip(queries, spotlight):
            ours = {Path(r.item.doc.id).stem for r in engine.search(query)}
            if ours != expected:
                diffs.append((query, sorted(ours), sorted(expected)))
        total += len(queries)
        agreed += len(queries) - len(diffs)
        print(f"{name:20s} {len(queries) - len(diffs):3d}/{len(queries)}")
        for query, ours, expected in diffs:
            print(f"    {query!r}: notes-search {ours}, Spotlight {expected}")
    print(f"{'total':20s} {agreed:3d}/{total}")


if __name__ == "__main__":
    main()
