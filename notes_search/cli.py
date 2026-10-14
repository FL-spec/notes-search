"""notes-search command line."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .engine import SearchEngine
from .query import TOKEN_ATTRIBUTES, MatchType, literal_substring_query, query_string
from .ranking import Strategy
from .sources import Document, load
from .text import highlight, search_terms

DEFAULT_INDEX = Path(os.environ.get("NOTES_SEARCH_INDEX", "~/.notes-search/index.json")).expanduser()

# Spotlight fields that exist for ordinary files, used by `spotlight`.
FILE_FIELDS = ("kMDItemDisplayName", "kMDItemAuthors", "kMDItemTextContent")

# `notes` is the query Notes.app actually runs; the others are the per-word clause builders.
MATCH_CHOICES = ["notes"] + [m.name.lower() for m in MatchType]


def _color() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _style(code: str) -> str:
    return f"\033[{code}m" if _color() else ""


def _build_query(text: str, match: str, fields=None) -> str:
    kwargs = {"fields": fields} if fields else {}
    if match == "notes":
        return literal_substring_query(text, **kwargs)
    return query_string(text, MatchType[match.upper()], **kwargs)


def _save(docs: list[Document], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"documents": [d.to_json() for d in docs]}, ensure_ascii=False))
    os.replace(tmp, path)


def _load_index(path: Path) -> list[Document]:
    if not path.exists():
        sys.exit(f"No index at {path}. Run `notes-search index SOURCE...` first, or pass --source.")
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Document.from_json(d) for d in data["documents"]]


def cmd_index(args) -> None:
    docs = load(args.sources, include_deleted=args.include_deleted)
    _save(docs, args.index)
    print(f"Indexed {len(docs)} documents into {args.index}")


def _print_results(engine: SearchEngine, results, args) -> None:
    if args.json:
        print(json.dumps([r.to_json() for r in results[: args.limit]], ensure_ascii=False, indent=2))
        return
    if not results:
        print("No results.")
        return
    shown = results[: args.limit]
    s = engine.sections(shown)
    query = getattr(args, "query", None)
    tokens = [t for t in search_terms(query) if t] if query else []
    bold, dim, reset = _style("1"), _style("2"), _style("0")
    hl = {"start": _style("22;1"), "end": _style("22;2")} if _color() else {"start": "", "end": ""}
    for name, group in (("Top Hits", s.top_hits), ("Notes", s.notes), ("Attachments", s.attachments)):
        if not group:
            continue
        print(f"\n{bold}{name}{reset}")
        for r in group:
            when = r.item.doc.modified.astimezone().strftime("%Y-%m-%d") if r.item.doc.modified else ""
            where = f" · {r.item.doc.folder}" if r.item.doc.folder else ""
            parent = f" · in “{r.item.doc.title}”" if r.item.result_type.name == "ATTACHMENT" else ""
            extra = (
                (
                    f"  [{r.source} score={r.ranking_score:g} bits={r.relevance_bit_field}"
                    f"{' exact-title' if r.is_exact_title_match else ''}"
                    f"{' prefix' if r.has_prefix_match else ''}]"
                )
                if args.verbose
                else ""
            )
            title = highlight(r.item.title, tokens, start=bold, end=reset) if _color() else r.item.title
            print(f"  {title}  {dim}{when}{where}{parent}{extra}{reset}")
            if r.snippet and r.snippet != r.item.title:
                print(f"    {dim}{highlight(r.snippet, tokens, **hl)}{reset}")
    if len(results) > len(shown):
        print(f"\n… {len(results) - len(shown)} more (use --limit)")


def _engine(args) -> SearchEngine:
    docs = load(args.source) if args.source else _load_index(args.index)
    return SearchEngine(
        docs,
        strategy=Strategy(args.strategy),
        fuzzy=args.fuzzy,
        natural_language=not args.no_nl,
        spell_expansion=args.spell,
        spell_api=args.spell_api,
        related_words=args.related,
        language=args.language,
    )


def cmd_search(args) -> None:
    engine = _engine(args)
    scope = args.has or []
    if args.explain:
        plan = engine.plan(args.query, scope)
        bold, reset = _style("1"), _style("0")
        if args.spell:
            print(
                f"{bold}tokens{reset} (word + spell-server variants): "
                + "; ".join(" | ".join(g) for g in plan.tokens)
            )
        for name, q in plan.queries:
            print(f"{bold}{name} query{reset}\n  {q}")
    _print_results(engine, engine.search(args.query, scope), args)


def cmd_interactive(args) -> None:
    engine = _engine(args)
    print(f"{len(engine.items)} items. Type a search, empty line to quit.")
    while True:
        try:
            q = input("search> ").strip()
        except EOFError:
            break
        if not q:
            break
        args.query = q
        _print_results(engine, engine.search(q, args.has or []), args)


def cmd_query(args) -> None:
    print(_build_query(args.text, args.match))


def cmd_spotlight(args) -> None:
    """Run Notes' query construction against files through mdfind."""
    if not shutil.which("mdfind"):
        sys.exit("mdfind not found (macOS only).")
    q = _build_query(args.text, args.match, fields=FILE_FIELDS)
    cmd = ["mdfind"]
    if args.onlyin:
        cmd += ["-onlyin", os.path.abspath(os.path.expanduser(args.onlyin))]
    cmd.append(q)
    if args.explain:
        print("Query: " + q, file=sys.stderr)
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode:
        sys.exit(out.stderr.strip())
    lines = out.stdout.splitlines()
    print("\n".join(lines[: args.limit]) if lines else "No results.")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(
        prog="notes-search", description="Search notes and text files exactly the way Apple Notes does."
    )
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--index", type=Path, default=DEFAULT_INDEX, help="index file (default: %(default)s)")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("index", help="build an index from folders, JSON files or 'apple-notes'")
    i.add_argument("sources", nargs="+")
    i.add_argument(
        "--skip-deleted",
        dest="include_deleted",
        action="store_false",
        help="leave out notes in Recently Deleted (apple-notes); Notes itself searches them",
    )
    # Accepted for compatibility with 1.0.2, where deleted notes were skipped by default.
    i.add_argument("--include-deleted", dest="include_deleted", action="store_true", help=argparse.SUPPRESS)
    i.set_defaults(func=cmd_index)

    def search_opts(sp):
        sp.add_argument(
            "--source", action="append", help="search these sources directly instead of the index"
        )
        sp.add_argument(
            "--strategy",
            type=int,
            choices=[0, 1, 2],
            default=0,
            help="ranking: 0 recency (Notes default), 1 score, 2 date buckets",
        )
        sp.add_argument(
            "--has",
            action="append",
            choices=sorted(TOKEN_ATTRIBUTES),
            help="scope token filter, like Notes' search tokens (repeatable)",
        )
        sp.add_argument(
            "--fuzzy",
            action="store_true",
            help="also run fuzzy and substring passes (Apple-internal setting)",
        )
        sp.add_argument(
            "--spell",
            action="store_true",
            help="also OR misspelled words with spell-server guesses (Notes' code path; "
            "measured to add nothing in Notes.app)",
        )
        sp.add_argument(
            "--spell-api",
            action="store_true",
            help="also run the spelling/lemma query (Apple-internal setting)",
        )
        sp.add_argument(
            "--related",
            action="store_true",
            help="also query related words from word embeddings (Apple-internal setting)",
        )
        sp.add_argument("--language", help="spell/embedding language, e.g. en (default: system)")
        sp.add_argument("--no-nl", action="store_true", help="disable date/folder/tag parsing")
        sp.add_argument("--limit", type=int, default=20)
        sp.add_argument("--json", action="store_true")
        sp.add_argument("-v", "--verbose", action="store_true", help="show match type and score")

    s = sub.add_parser("search", help="search the index")
    s.add_argument("query")
    s.add_argument("--explain", action="store_true", help="print the Spotlight queries Notes builds")
    search_opts(s)
    s.set_defaults(func=cmd_search)

    it = sub.add_parser("interactive", help="search repeatedly")
    search_opts(it)
    it.set_defaults(func=cmd_interactive)

    q = sub.add_parser("query", help="print the Spotlight query string Notes would build")
    q.add_argument("text")
    q.add_argument(
        "--match",
        choices=MATCH_CHOICES,
        default="notes",
        help="notes (default): the query Notes runs; or one per-word clause type",
    )
    q.set_defaults(func=cmd_query)

    sp = sub.add_parser("spotlight", help="run the Notes-style query against files via mdfind")
    sp.add_argument("text")
    sp.add_argument("--onlyin", help="limit to a folder")
    sp.add_argument("--match", choices=MATCH_CHOICES, default="notes")
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--explain", action="store_true")
    sp.set_defaults(func=cmd_spotlight)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
