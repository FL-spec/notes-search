# notes-search

Search your notes and text files the way Apple Notes does.

notes-search is a small Python tool that reproduces the search behaviour of Apple Notes on
macOS: which notes match, in what order, and which two become Top Hits. It was built by
reverse engineering Notes and Spotlight on macOS 26, then checked against the real app search
by search. It has no dependencies beyond the Python standard library.

```console
$ notes-search search "ill"

Top Hits
  Shopping list  2026-10-13
    …[x] milk - [ ] bread - [ ] vanilla
  Crème brûlée  2026-10-13 · Cooking
    Cream, vanilla, sugar, egg yolks. Bake in a water bath.
```

`ill` finds *milk* and *vanilla* because Notes matches the first word you type anywhere inside a
word, not just at the start.

## Contents

- [How Notes search works](#how-notes-search-works)
- [Install](#install)
- [Usage](#usage)
- [Using it as a library](#using-it-as-a-library)
- [Accuracy](#accuracy)
- [Limitations](#limitations)
- [Project layout](#project-layout)
- [Development](#development)
- [License and disclaimer](#license-and-disclaimer)

## How Notes search works

The short version. The [full explanation](docs/how-it-works.md) covers each rule and where it
comes from.

1. **One query, built from your whole search.** Notes wraps everything you type in wildcards
   and hands it to Spotlight as `"*your search*"cwdt`. It matches case-insensitively, ignores
   accents, works on word boundaries, and splits the text into words.
2. **The first word can match anywhere inside a word.** `ack` finds *back*. Every later word has
   to match the start of a word, so `orvat release` finds *Quorvatix … release notes*, but
   `release orvat` finds nothing.
3. **All words must appear in the same field.** The body field includes the title line, so a
   word from the title and a word from the body can combine.
4. **Punctuation follows Spotlight's indexing.** `alpha-beta`, emails, URLs and numbers like
   `3.14` are stored whole and in parts, so `example.com` finds *user@example.com*. The details
   are in the [tokenization table](docs/how-it-works.md#how-text-is-split-into-searchable-words).
5. **Typos are not corrected.** Notes asks the system spell checker for guesses, but in practice
   the guesses never add results.
6. **Results are ordered by date.** An exact title match comes first, then notes before
   attachments, then the most recently modified. Relevance scores exist but only break ties.
7. **The first two results are Top Hits.** Nothing more than that.
8. **Dates in the search act as filters.** "last month" means from the first day of the previous
   month until now, and "from july" means July.

## Install

Requires Python 3.10 or later. macOS is needed only for the Apple Notes import and the
Spotlight commands; searching folders and JSON files works anywhere.

```bash
pipx install git+https://github.com/FL-spec/notes-search
```

Or from a clone:

```bash
pip install .
```

You can also run it without installing, from the repository root, with `python3 -m notes_search`.

## Usage

### Build an index

```bash
notes-search index ~/Documents/notes
notes-search index apple-notes
notes-search index ~/notes backup.json apple-notes
```

Sources can be folders of `.md`, `.txt`, `.org` or `.rst` files, JSON files, or the word
`apple-notes`. Several sources can go into one index.

- **Folders:** the first non-empty line of each file becomes its title, as in Notes.
- **Apple Notes:** read through JavaScript for Automation. macOS asks once for permission to
  control Notes. Locked notes are indexed by title only, which is also what Notes itself does.

The index is a JSON file at `~/.notes-search/index.json`. It holds the full text of everything
you index, so treat it like the notes themselves. Use `--index PATH` or `NOTES_SEARCH_INDEX` to
put it elsewhere.

### Search

```bash
notes-search search "lisbon tram"
notes-search search "budget last month"
notes-search search "milk" --has checklists
notes-search search "taxes" --verbose
notes-search search "taxes" --json
notes-search interactive
```

| Option | What it does |
| --- | --- |
| `--has NAME` | Keep only notes with an attribute, like the filter chips in Notes: `attachments`, `checklists`, `drawings`, `scans`, `tags`, `locked`, `shared`. Repeatable. Works without search text. |
| `--strategy 0\|1\|2` | Sort order. `0` is what Notes uses. `1` sorts by relevance score, `2` by age bucket and then score. Both exist in Notes' code but aren't used by the app. |
| `--explain` | Print the Spotlight queries Notes would run for this search. |
| `--verbose` | Show which query found each result, its score and which fields matched. |
| `--source PATH` | Search a folder or file directly, skipping the index. Repeatable. |
| `--no-nl` | Treat date phrases, `folder:` and `#tags` as plain text. |
| `--json`, `--limit N` | Machine-readable output; result count. |

Notes contains several search features that are switched off in the shipping app. They are
available here for experimenting, and none of them is on by default:

| Option | Feature |
| --- | --- |
| `--fuzzy` | Typo-tolerant pass using Spotlight's `f066` fuzzy matching, plus a substring pass |
| `--spell` | Add the system spell checker's guesses for misspelled words |
| `--spell-api` | Extra exact queries for the spell checker's first guess and the word's lemma |
| `--related` | Extra queries for related words from Apple's word embeddings |

### Spotlight commands

`query` prints the query Notes would build. `spotlight` runs the same query style against files
on your Mac through `mdfind`, so you can search ordinary documents with Notes' rules.

```bash
notes-search query "lisbon tram"
notes-search spotlight "invoice march" --onlyin ~/Documents
```

## Using it as a library

```python
from notes_search.engine import SearchEngine
from notes_search.sources import from_folder

engine = SearchEngine(from_folder("~/notes"))

for result in engine.search("lisbon tram"):
    print(result.item.title, result.is_top_hit, result.ranking_score)
```

`SearchEngine.search` returns results in Notes' order. `SearchEngine.sections` groups them into
Top Hits, Notes and Attachments, and `search_batched` yields them in batches the way Notes fills
its list. Documents can come from anywhere: build `notes_search.sources.Document` objects
yourself if your notes live in a database or an API.

## Accuracy

The rules were checked two ways: against Notes.app itself, comparing the notes returned, their
order and the Top Hits, and against Spotlight, the engine Notes hands its query to.

- **A real 17-note library:** 54 of 54 searches identical, including 12 whose results were
  predicted before running them in Notes.
- **A synthetic corpus run through Spotlight itself:** 414 of 417 queries identical, including
  71 written after the last change and never used to make one. The corpus covers accents in
  several languages, ß, ligatures, camelCase, `node.js`, email addresses, URLs, numbers, emoji,
  Japanese text and apostrophes.
- **The fuzzy matcher** (`--fuzzy`): compared with real Spotlight on 295 typo variants at four
  thresholds.

[docs/validation.md](docs/validation.md) has the method, every case and every result.

## Limitations

- **Natural language.** Notes parses phrases like "photos from last summer" with a private Apple
  parser. notes-search handles the date phrases that were measured, plus `folder:` and `#tags`,
  but not Apple's full grammar.
- **Attachment details.** Drawings and scanned documents can't be told apart through Notes'
  scripting interface, so `--has drawings` and `--has scans` only work for sources that set those
  flags.
- **Photo recognition.** Notes also searches the labels its on-device image recognition assigns
  to photos. notes-search searches those labels if a source provides them, but doesn't generate
  them.
- **A few Spotlight quirks aren't modelled.** Fragments that start at an inner apostrophe
  (`n'roll` in *rock'n'roll*) and certain single-digit searches behave differently; see
  [validation.md](docs/validation.md#2-spotlight-on-a-synthetic-corpus).
- **One version tested.** The behaviour was measured on macOS 26. Apple can change search in any
  release.

## Project layout

```
notes_search/
  engine.py    search pipeline: matching, query passes, ordering, Top Hits
  query.py     Spotlight query construction, tokenizer, scope tokens
  text.py      text folding, word splitting and Spotlight's fuzzy matcher
  ranking.py   relevance weights, date buckets and sort strategies
  nl.py        date phrases, folder: and #tag filters
  sources.py   folders, JSON and the Apple Notes export
  macos.py     spell checker and word embeddings through osascript
  cli.py       command-line interface
docs/
  how-it-works.md        every rule, explained
  reverse-engineering.md how the rules were found
  validation.md          how they were checked
scripts/
  matching_lab.py        compares matching with real Spotlight on the synthetic corpus
  fuzzy_lab.py           compares the fuzzy matcher with real Spotlight
  lab/                   the corpus and the 417 lab queries
examples/notes/          sample notes to try it on
```

## Development

```bash
python3 -m unittest discover -s tests
python3 scripts/matching_lab.py fuzzylab/corpus   # macOS only, several minutes
python3 scripts/fuzzy_lab.py fuzzylab/typos        # macOS only
```

The unit tests run anywhere. Tests that need macOS services skip themselves elsewhere. See
[CONTRIBUTING.md](CONTRIBUTING.md) before changing matching or ordering: those rules came from
measurements, and a change needs a measurement too.

## License and disclaimer

MIT, see [LICENSE](LICENSE).

notes-search is an independent project. It is not affiliated with or endorsed by Apple. It
contains no Apple code: the behaviour was worked out by studying the shipped software and
observing it, and this is a fresh implementation of what was learned. Apple and Apple Notes are
trademarks of Apple Inc.
