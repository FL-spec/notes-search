"""Render docs/assets/demo.svg: real notes-search output on examples/notes, as a terminal window.

    python3 scripts/render_demo.py

The searches run through the engine, so the picture always shows what the code actually does.
"""

from __future__ import annotations

import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from notes_search.engine import SearchEngine  # noqa: E402
from notes_search.sources import from_folder  # noqa: E402
from notes_search.text import highlight, search_terms  # noqa: E402

SEARCHES = ["ill", "belem tram"]
WIDTH, LINE, PAD = 760, 21, 22

COLORS = {
    "bg": "#16181d",
    "bar": "#22252c",
    "text": "#d7dae0",
    "dim": "#7d8590",
    "prompt": "#7ee787",
    "head": "#e6edf3",
    "hit": "#f2cc60",
}


def spans(text: str, terms: list[str], base: str) -> str:
    """Escape text, colouring the words that contain a search term (as the CLI does)."""
    marked = highlight(text, terms, start="\x00", end="\x01")
    out = []
    for i, chunk in enumerate(marked.split("\x00")):
        if i:
            word, rest = chunk.split("\x01", 1)
            out.append(f'<tspan fill="{COLORS["hit"]}" font-weight="600">{escape(word)}</tspan>')
            chunk = rest
        if chunk:
            out.append(f'<tspan fill="{base}">{escape(chunk)}</tspan>')
    return "".join(out)


def build_lines() -> list[str]:
    engine = SearchEngine(list(from_folder(ROOT / "examples" / "notes")))
    lines: list[str] = []
    for n, search in enumerate(SEARCHES):
        if n:
            lines.append("")
        lines.append(
            f'<tspan fill="{COLORS["prompt"]}">$</tspan> '
            f'<tspan fill="{COLORS["text"]}">notes-search search "{escape(search)}"</tspan>'
        )
        results = engine.search(search)
        terms = [t for t in search_terms(search) if t]
        sections = engine.sections(results)
        for name, group in (("Top Hits", sections.top_hits), ("Notes", sections.notes)):
            if not group:
                continue
            lines.append("")
            lines.append(f'<tspan fill="{COLORS["head"]}" font-weight="700">{name}</tspan>')
            for r in group:
                folder = f" · {r.item.doc.folder}" if r.item.doc.folder else ""
                lines.append(
                    "  "
                    + spans(r.item.title, terms, COLORS["text"])
                    + f'<tspan fill="{COLORS["dim"]}">{escape(folder)}</tspan>'
                )
                if r.snippet and r.snippet != r.item.title:
                    snippet = r.snippet if len(r.snippet) <= 78 else r.snippet[:77] + "…"
                    lines.append("    " + spans(snippet, terms, COLORS["dim"]))
    return lines


def render(lines: list[str]) -> str:
    top = 38
    height = top + PAD + LINE * len(lines) + PAD - 6
    rows = "\n".join(
        f'  <text x="{PAD}" y="{top + PAD + 10 + i * LINE}" xml:space="preserve">{line}</text>'
        for i, line in enumerate(lines)
    )
    dots = "".join(
        f'<circle cx="{20 + i * 20}" cy="19" r="6" fill="{c}"/>'
        for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840"))
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" \
viewBox="0 0 {WIDTH} {height}" role="img" aria-label="notes-search finding notes the way Apple Notes does">
  <title>notes-search finding notes the way Apple Notes does</title>
  <rect width="{WIDTH}" height="{height}" rx="10" fill="{COLORS["bg"]}"/>
  <path d="M0 10a10 10 0 0 1 10-10h{WIDTH - 20}a10 10 0 0 1 10 10v28H0z" fill="{COLORS["bar"]}"/>
  {dots}
  <g font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="14">
{rows}
  </g>
</svg>
"""


def main() -> None:
    out = ROOT / "docs" / "assets" / "demo.svg"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(build_lines()), encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
