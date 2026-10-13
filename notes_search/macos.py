"""macOS language services used by Notes search, reached through JavaScript for Automation.

Notes calls the system spell server (ICSearchQueryTokenizer), NSLinguisticTagger lemmas and
NLEmbedding neighbours. The same services are available to ``osascript -l JavaScript``, so
this module needs no third-party packages. Every function degrades to "no data" when the
services are unavailable (non-macOS, sandbox, timeout).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from functools import lru_cache

_SCRIPT = r"""
ObjC.import('AppKit'); ObjC.import('NaturalLanguage');
function arr(a) { return a && a.js ? a.js.map(x => x.js) : []; }
function run(argv) {
  const req = JSON.parse(argv[0]);
  const out = {guesses: {}, lemmas: {}, neighbors: {}};
  const sc = $.NSSpellChecker.sharedSpellChecker;
  const lang = req.language || sc.language.js;
  for (const w of req.spell || []) {
    const s = $(w);
    const miss = sc.checkSpellingOfStringStartingAt(s, 0).length > 0;
    out.guesses[w] = miss ? arr(sc.guessesForWordRangeInStringLanguageInSpellDocumentWithTag(
        $.NSMakeRange(0, w.length), s, $(lang), 0)) : null;
  }
  for (const w of req.lemma || []) {
    const t = $.NLTagger.alloc.initWithTagSchemes($([$.NLTagSchemeLemma]));
    t.string = $(w);
    const tag = t.tagAtIndexUnitSchemeTokenRange(0, $.NLTokenUnitWord, $.NLTagSchemeLemma, null);
    out.lemmas[w] = tag && tag.js ? tag.js : null;
  }
  if ((req.related || []).length) {
    const emb = $.NLEmbedding.wordEmbeddingForLanguage($(lang.split('_')[0]));
    for (const w of req.related) {
      out.neighbors[w] = emb && emb.js !== undefined
        ? arr(emb.neighborsForStringMaximumCountMaximumDistanceDistanceType(
            $(w), req.maxCount || 5, req.maxDistance || 1.0, 0)) : [];
    }
  }
  return JSON.stringify(out);
}
"""


def available() -> bool:
    return shutil.which("osascript") is not None


@lru_cache(maxsize=512)
def _call(payload: str) -> dict:
    if not available():
        return {}
    try:
        proc = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", _SCRIPT, payload],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if proc.returncode != 0:
        return {}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def language_services(
    spell: tuple[str, ...] = (),
    lemma: tuple[str, ...] = (),
    related: tuple[str, ...] = (),
    language: str | None = None,
    max_count: int = 5,
    max_distance: float = 1.0,
) -> dict:
    """One round trip for all lookups. Returns {'guesses', 'lemmas', 'neighbors'} dicts.

    ``guesses[w]`` is None when the word is spelled correctly (Notes only expands
    misspelled words), otherwise the spell server's guesses in rank order.
    """
    payload = json.dumps({
        "spell": list(spell), "lemma": list(lemma), "related": list(related),
        "language": language, "maxCount": max_count, "maxDistance": max_distance,
    })
    data = _call(payload)
    return {
        "guesses": data.get("guesses", {}),
        "lemmas": data.get("lemmas", {}),
        "neighbors": data.get("neighbors", {}),
    }
