"""Local evaluation of Spotlight's string matching.

Spotlight compares a query against the *tokens* it stored when indexing, not against raw text.
The tokenization below was measured with ``mdfind`` (see docs/validation.md):

* Text is split on whitespace. Leading and trailing punctuation is dropped from each piece, so
  ``#plan`` and ``websites:`` index as ``plan`` and ``websites``.
* Each piece is indexed whole (``quorvatix-beta``, ``user@example.com``, ``3.14.159``) and also
  as its letter runs and digit runs (``quorvatix``, ``beta``; ``user``, ``example``, ``com``;
  ``abc123def`` → ``abc``, ``123``, ``def``).
* An apostrophe joins a contraction or possessive into one token (``can't``, ``maribel's``),
  but splits an elision before a longer word (``o'brien`` → ``o``, ``brien``).
* Tails after ``.`` ``,`` ``@`` ``-`` ``_`` and camelCase parts are indexed too.
* ``c`` and ``d`` fold case and accents, and ``ß`` matches ``ss``. Compatibility characters
  such as the ligature ``ﬁ`` are *not* folded: ``financial`` does not match ``ﬁnancial``.

The fuzzy modifier ``fNNN`` is implemented as in MetadataUtilities ``fuzzy_matches`` /
``lev_ukkonen_accepts``: Levenshtein distance with an edit budget of
``min(3, floor(len(pattern) * (100 - NNN) / 100))``.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from .query import MatchType

_APOSTROPHES = "'’"
# Separators inside a piece that delimit sub-compounds (URL parts, key=value, lists), while
# word-internal connectors such as . @ - _ ' stay: https://host.example.org/path?q=x indexes
# host.example.org, path and q=x as well as the whole piece.
_SUBCOMPOUND_RE = re.compile(r"[/:?&#;()\[\]{}<>!\"|]+")
# Connectors after which Spotlight also indexes the rest of the compound (example.com, .js).
_TAIL_SEPARATORS = ".,@-_"
_RUN_RE = re.compile(r"\d+|[^\W\d_]+", re.UNICODE)
_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")
_DIGIT_LETTER_RE = re.compile(r"(?<=\d)(?=[^\W\d_])", re.UNICODE)

# Notes always uses f066.
FUZZY_THRESHOLD = 66


@lru_cache(maxsize=65536)
def fold(text: str) -> str:
    """Case- and diacritic-fold, like Spotlight's ``c`` and ``d`` flags."""
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.lower().replace("ß", "ss")


def _strip_punctuation(piece: str) -> str:
    start, end = 0, len(piece)
    while start < end and unicodedata.category(piece[start]).startswith("P"):
        start += 1
    while end > start and unicodedata.category(piece[end - 1]).startswith("P"):
        end -= 1
    return piece[start:end]


def _rstrip_punctuation(piece: str) -> str:
    end = len(piece)
    while end > 0 and unicodedata.category(piece[end - 1]).startswith("P"):
        end -= 1
    return piece[:end]


def _letters_after(text: str, i: int) -> int:
    n = 0
    while i + n < len(text) and text[i + n].isalpha():
        n += 1
    return n


def _elision_split(piece: str) -> list[str]:
    """Split at the first apostrophe if three or more letters follow it (l'amour, o'neil);
    contractions and possessives (isn't, they'll, maribel's, rock'n'roll) stay whole."""
    for i, ch in enumerate(piece):
        if ch in _APOSTROPHES:
            if _letters_after(piece, i + 1) >= 3:
                return [piece[:i], piece[i + 1:]]
            break
    return [piece]


@lru_cache(maxsize=16384)
def words(text: str) -> tuple[str, ...]:
    """The tokens Spotlight indexes for ``text``, folded. Measured with mdfind:

    * each whitespace piece, with trailing punctuation removed and leading punctuation kept
      (``#plan``), and again with both removed;
    * its sub-compounds between URL-style separators (``host.example.org``, ``q=x``);
    * the tail after every ``.`` ``,`` ``@`` ``-`` ``_``, with and without the separator
      (``example.com``, ``.js``, ``case_value``);
    * its letter runs and digit runs, split again at camelCase boundaries
      (``ABC123def`` → ``abc``, ``123``, ``def``; ``ParseThing`` → ``parse``, ``thing``), and
      its pieces between digit→letter changes (``zx2026q3`` → ``zx2026``, ``q3``);
    * around apostrophes: the first part, and later parts of three or more letters.
    """
    out: dict[str, None] = {}

    def add(token: str) -> None:
        if token:
            out.setdefault(fold(token))

    for raw in text.split():
        for segment in _elision_split(_rstrip_punctuation(raw)):
            add(segment)
            core = _strip_punctuation(segment)
            if not core:
                continue
            add(core)
            for sub in _SUBCOMPOUND_RE.split(core):
                sub = _strip_punctuation(sub)
                add(sub)
                for i, ch in enumerate(sub):
                    if ch in _TAIL_SEPARATORS and i:
                        add(sub[i:])
                        add(sub[i + 1:])
            pieces = re.split(f"[{_APOSTROPHES}]", core)
            kept = pieces[:1] + [p for p in pieces[1:] if sum(c.isalpha() for c in p) >= 3]
            for piece in kept:
                for chunk in _DIGIT_LETTER_RE.split(piece):  # zx2026q3 → zx2026, q3
                    add(chunk)
                for run in _RUN_RE.findall(piece):
                    add(run)
                    for part in _CAMEL_RE.split(run):
                        add(part)
    return tuple(out)


# Trailing characters that a word may end with and still match (a dangling connector).
_CONNECTORS = "-'’"


def _first_term(raw: str) -> str | None:
    """The leading-wildcard word, as Spotlight treats it (measured with mdfind).

    Punctuation is kept and matched literally: ``#plan`` finds ``#planning`` but not ``plan``.
    If the word ends in punctuation and the rest of it has none of its own, it can't match at
    all: ``testers.``, ``zxsite.`` and ``user@`` find nothing, while ``3.14.``,
    ``user@example.``, ``quorvatix-`` and ``maribel'`` match.
    """
    term = raw.lstrip(_CONNECTORS)  # a leading apostrophe or hyphen is ignored ('amour)
    end = len(term)
    while end > 0 and unicodedata.category(term[end - 1]).startswith("P") and term[end - 1] not in _CONNECTORS:
        end -= 1
    core, trailing = term[:end], term[end:]
    if not core:
        return None
    if trailing and not any(unicodedata.category(c).startswith("P") for c in core):
        return None
    return term


def search_terms(search: str) -> list[str | None]:
    """The words of a search as Spotlight's ``t`` flag splits them, folded.

    Words are separated by whitespace only, so ``quorvatix-beta`` stays one word. The first
    word is matched inside indexed tokens and keeps its punctuation (see ``_first_term``; it is
    ``None`` when it can't match anything). Later words are matched as prefixes, with
    punctuation at either end ignored.
    """
    pieces = fold(search).split()
    if not pieces:
        return []
    rest = [t for t in (_strip_punctuation(p) for p in pieces[1:]) if t]
    return [_first_term(pieces[0]), *rest]


def fuzzy_budget(pattern_length: int, threshold: int = FUZZY_THRESHOLD) -> int:
    """Edit budget: min(3, floor(len * (100 - threshold) / 100))."""
    return min(3, pattern_length * (100 - threshold) // 100)


def levenshtein_within(a: str, b: str, limit: int) -> bool:
    """Plain Levenshtein (a transposition costs 2), banded, with early exit."""
    if abs(len(a) - len(b)) > limit:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        best = i
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            best = min(best, cur[j])
        if best > limit:
            return False
        prev = cur
    return prev[-1] <= limit


@lru_cache(maxsize=65536)
def fuzzy_word_match(pattern: str, word: str, prefix: bool, threshold: int = FUZZY_THRESHOLD) -> bool:
    """fuzzy_matches(): whole-word match, or (prefix mode) a word prefix whose length is
    within the edit budget of the pattern length."""
    k = fuzzy_budget(len(pattern), threshold)
    if levenshtein_within(pattern, word, k):
        return True
    if prefix:
        lo, hi = max(0, len(pattern) - k), min(len(word), len(pattern) + k)
        return any(levenshtein_within(pattern, word[:n], k) for n in range(lo, hi + 1))
    return False


def token_matches(token: str, value: str, match_type: MatchType) -> bool:
    """Does one (already folded) token match a field value under the given match type?"""
    if not value:
        return False
    value_words = words(value)
    if match_type is MatchType.SUBSTRING:
        return any(token in w for w in value_words)
    if match_type is MatchType.EXACT:
        return token in value_words
    if match_type is MatchType.PREFIX:
        return any(w.startswith(token) for w in value_words)
    return any(w.startswith(token) or fuzzy_word_match(token, w, prefix=True) for w in value_words)


def highlight(text: str, tokens: list[str], start: str = "\x1b[1m", end: str = "\x1b[22m") -> str:
    """Wrap the words of ``text`` that contain any search term."""
    if not tokens:
        return text

    def repl(m: re.Match) -> str:
        w = fold(m.group(0))
        return f"{start}{m.group(0)}{end}" if any(t in w for t in tokens) else m.group(0)

    return re.sub(r"\S+", repl, text)
