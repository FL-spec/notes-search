# Security policy

notes-search reads personal data: the full text of your notes. Problems that could expose that
data are treated as security issues.

## Reporting a vulnerability

Please report it privately through
[GitHub's private vulnerability reporting](https://github.com/FL-spec/notes-search/security/advisories/new)
rather than in a public issue. Include the steps to reproduce it and the version you used
(`notes-search --version`).

## What notes-search does with your data

- It never sends anything over the network: there is no telemetry and no update check, and the
  code makes no network requests.
- The index is a local JSON file (`~/.notes-search/index.json` by default) containing the
  full text of every note you indexed. Anyone who can read that file can read your notes, so keep
  it where you would keep the notes themselves, and delete it when you no longer need it.
- The Apple Notes import uses macOS automation. macOS asks once for permission for your
  terminal to control Notes, and you can revoke it in System Settings › Privacy & Security ›
  Automation. Locked notes are exported by title only; their contents are never read.
- The optional language features (`--spell`, `--spell-api`, `--related`) pass search words to
  the macOS spell checker and word embeddings through `osascript`. They run on your Mac.

## Supported versions

Only the latest release receives fixes.
