## What this changes

<!-- One or two sentences. Link the issue if there is one. -->

## How it was checked

<!--
Changes to matching or ordering need evidence from Notes.app or Spotlight:
the notes, the search, and what the app showed. Add the case as a test in
tests/test_engine.py, and run scripts/matching_lab.py if you changed tokenization.
-->

- [ ] `python3 -m unittest discover -s tests` passes
- [ ] `ruff check .`, `ruff format --check .` and `mypy notes_search` pass
- [ ] Docs updated if behaviour changed
- [ ] No real notes or personal data in tests, fixtures or commits
