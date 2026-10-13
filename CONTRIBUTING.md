# Contributing

Thanks for taking an interest. A few things are worth knowing before you change anything.

## The rules come from measurements

Every matching and ordering rule in notes-search was checked against Notes.app (see
[docs/validation.md](docs/validation.md)). If you think a rule is wrong, the most useful thing you
can send is a reproducible case:

1. the text of one or two notes, made up for the purpose rather than taken from your own notes;
2. the search you typed;
3. what Notes.app showed, in order, including which results were under Top Hits;
4. your macOS version.

A pull request that changes matching or ordering should include that kind of case as a test in
`tests/test_engine.py`, in `MeasuredNotesBehaviourTests`.

## Running the tests

```bash
python3 -m unittest discover -s tests
```

No dependencies are needed. Tests that use macOS services skip themselves when those services
aren't available.

To check matching and the fuzzy matcher against Spotlight on your Mac:

```bash
python3 scripts/matching_lab.py fuzzylab/corpus
python3 scripts/fuzzy_lab.py fuzzylab/typos
python3 scripts/render_demo.py     # after changing output formatting
```

The folder must not be hidden or inside a hidden folder, because Spotlight doesn't index those.
If you change tokenization, add the queries you used to `scripts/lab/queries.json` and run the
matching lab before and after.

## Style

The project uses `ruff` for linting and formatting and `mypy` for type checking. CI runs all
three, so it helps to run them before pushing:

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy notes_search
```

- Standard library only. The project is meant to be read, and a dependency has to be worth it.
- When code mirrors something in Apple's frameworks, name the Apple symbol in a comment, as the
  existing code does. It is the only way the next person can check it.
- Keep private data out of the repository. Don't paste real notes into tests, issues or commits.
