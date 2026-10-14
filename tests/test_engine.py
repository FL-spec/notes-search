import math
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from notes_search import macos, nl
from notes_search.engine import SearchEngine
from notes_search.query import MatchType, joint_query, max_variants_per_token, query_tokens
from notes_search.ranking import Field, ResultType, Strategy, bucket_of_time_interval, field_score
from notes_search.sources import Document, html_to_text, text_flags
from notes_search.text import fold, fuzzy_budget, fuzzy_word_match, token_matches

NOW = datetime.now(timezone.utc)


def doc(i, title, body="", days_old=0, **kw):
    t = NOW - timedelta(days=days_old)
    return Document(id=str(i), title=title, body=body, modified=t, created=t, **kw)


def engine(docs, **kw):
    kw.setdefault("spell_expansion", False)  # keep unit tests independent of macOS services
    return SearchEngine(docs, **kw)


def ids(results):
    return [r.item.doc.id + ("/att" if r.item.result_type is ResultType.ATTACHMENT else "") for r in results]


class TextTests(unittest.TestCase):
    def test_case_and_diacritic_insensitive(self):
        self.assertEqual(fold("Crème BRÛLÉE"), "creme brulee")

    def test_prefix_is_word_based(self):
        self.assertTrue(token_matches("rec", "Grandma's recipes", MatchType.PREFIX))
        self.assertFalse(token_matches("ipe", "recipes", MatchType.PREFIX))

    def test_substring_matches_inside_words(self):
        self.assertTrue(token_matches("ipe", "recipes", MatchType.SUBSTRING))


class SpotlightFuzzyTests(unittest.TestCase):
    """Recovered from MetadataUtilities fuzzy_matches and confirmed with mdfind."""

    def test_budget_formula(self):
        self.assertEqual([fuzzy_budget(n) for n in (2, 3, 4, 6, 8, 9, 20)], [0, 1, 1, 2, 2, 3, 3])

    def test_observed_matches(self):
        for word in ("recpie", "reicpe", "rexipe", "recip", "recipr", "racipo", "arecipe"):
            self.assertTrue(fuzzy_word_match("recipe", word, prefix=True), word)

    def test_observed_rejections(self):
        self.assertFalse(fuzzy_word_match("recipe", "rcp", prefix=True))  # 3 edits > 2
        self.assertFalse(fuzzy_word_match("cake", "acke", prefix=False))  # swap = 2 edits > 1

    def test_transposition_costs_two(self):
        self.assertTrue(fuzzy_word_match("chocolate", "cohcolate", prefix=False))  # budget 3
        self.assertFalse(fuzzy_word_match("cake", "ckae", prefix=False))  # budget 1


class TokenizerTests(unittest.TestCase):
    def test_connectors(self):
        self.assertEqual(
            query_tokens("e-mail (draft) don't"), [("e-mail", False), ("draft", True), ("don't", False)]
        )

    def test_variant_cap(self):
        self.assertEqual([max_variants_per_token(n) for n in (1, 2, 3, 7)], [60, 30, 20, 8])

    def test_joint_query(self):
        self.assertEqual(
            joint_query("(x)", ["checklists", "locked"]),
            "((_ICItemHasChecklists = 1) && (_ICItemIsLocked = 1)) && ((x))",
        )


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.docs = [
            doc(1, "Shopping list", "eggs milk bread", days_old=1),
            doc(2, "Recipes", "pancakes need eggs and milk", days_old=10),
            doc(3, "Trip to Lisbon", "pastéis de nata", days_old=3),
        ]

    def test_all_words_must_match(self):
        self.assertEqual(ids(engine(self.docs).search("eggs pancakes")), ["2"])

    def test_words_may_match_different_fields(self):
        self.assertEqual(ids(engine(self.docs).search("trip nata")), ["3"])

    def test_diacritics_ignored(self):
        self.assertEqual(len(engine(self.docs).search("pasteis")), 1)

    def test_typos_do_not_match_by_default(self):
        self.assertEqual(engine(self.docs).search("pancaeks"), [])

    def test_fuzzy_when_enabled(self):
        self.assertEqual(ids(engine(self.docs, fuzzy=True).search("pancaeks")), ["2"])

    def test_scope_tokens(self):
        docs = [doc(1, "Groceries", "- [ ] milk", flags=["checklists"]), doc(2, "Milk facts", "milk")]
        self.assertEqual(ids(engine(docs).search("milk", scope=["checklists"])), ["1"])

    def test_scope_tokens_without_text(self):
        docs = [doc(1, "Diary", flags=["locked"]), doc(2, "Open note")]
        self.assertEqual(ids(engine(docs).search("", scope=["locked"])), ["1"])


class MeasuredNotesBehaviourTests(unittest.TestCase):
    """Cases observed side by side in Notes.app (macOS 26), replayed on synthetic notes."""

    def setUp(self):
        self.docs = [
            doc("a", "Quorvatix 20 Release", "Fix names on the roadmap", days_old=0),
            doc("b", "add a button", "to go back to the list", days_old=14),
            doc("c", "Garden", "areia macia", days_old=76),
            doc("d", "Config", "API_TOKEN and the GITHUB_REPO_OWNER", days_old=40),
        ]

    def q(self, text):
        return ids(engine(self.docs).search(text))

    def test_single_word_matches_inside_words(self):
        self.assertEqual(self.q("ack"), ["b"])  # b-ack
        self.assertEqual(self.q("acia"), ["c"])  # m-acia
        self.assertEqual(self.q("orvat"), ["a"])
        self.assertEqual(self.q("b"), ["b", "d"])  # b-utton, GITHU-B

    def test_first_word_substring_later_words_prefix(self):
        self.assertEqual(self.q("orvatix release"), ["a"])
        self.assertEqual(self.q("orvatix names"), ["a"])  # title and body: one text field
        self.assertEqual(self.q("ames roadmap"), ["a"])
        self.assertEqual(self.q("quo names"), ["a"])
        self.assertEqual(self.q("orvatix 2 release"), ["a"])  # middle word: prefix of "20"
        self.assertEqual(self.q("orvatix lease"), [])
        self.assertEqual(self.q("quorvatix lease"), [])
        self.assertEqual(self.q("release orvatix"), [])
        self.assertEqual(self.q("xyzq release"), [])

    def test_underscore_joined_words(self):
        self.assertEqual(self.q("api token"), ["d"])  # API_TOKEN splits into two words
        self.assertEqual(self.q("repo"), ["d"])

    def test_order_is_exact_title_then_date_not_prefix(self):
        docs = [
            doc("old", "k7", "x", days_old=76),
            doc("new", "Keys", "token_51K7x", days_old=40),
            doc("sub", "Other", "zzk7zz", days_old=1),
        ]
        self.assertEqual(ids(engine(docs).search("k7")), ["old", "sub", "new"])

    def test_typos_are_not_corrected(self):
        self.assertEqual(self.q("quorvatik"), [])
        self.assertEqual(self.q("roadmpa"), [])


class SpotlightTokenizationTests(unittest.TestCase):
    """Rules measured by running Notes' query through mdfind on a synthetic corpus."""

    def setUp(self):
        self.docs = [
            doc("hy", "Plan", "Call about the alpha-beta contract. The e-mail was sent on Friday."),
            doc("sp", "Plan", "Ship the alpha beta to testers."),
            doc("url", "Contact", "Write to user@example.com or open https://site.example.org/path?q=query"),
            doc("num", "Numbers", "version 3.14.159 and 42nd street and ABC123def, total 1.299,50"),
            doc("tag", "Hashtag note", "Plan for #project and @person"),
            doc("apo", "Apostrophes", "don't can't zxo'brien rock'n'roll Maribel's"),
            doc("lig", "Ligature", "ﬁnancial oﬃce"),
            doc("acc", "Straße", "Crème brûlée, naïve"),
            doc("emo", "Party", "Balloons 🎉 tonight"),
        ]

    def q(self, text):
        return sorted(ids(engine(self.docs, natural_language=False).search(text)))

    def test_compounds_are_indexed_whole_and_in_parts(self):
        self.assertEqual(self.q("alpha-beta"), ["hy"])  # hyphen kept in the search word
        self.assertEqual(self.q("alpha beta"), ["hy", "sp"])
        self.assertEqual(self.q("call beta"), ["hy"])  # later word: part of a compound
        self.assertEqual(self.q("write com"), ["url"])
        self.assertEqual(self.q("street def"), ["num"])  # letter/digit runs
        self.assertEqual(self.q("total 50"), ["num"])

    def test_url_pieces(self):
        self.assertEqual(self.q("write site.example"), ["url"])
        self.assertEqual(self.q("example.org"), ["url"])

    def test_first_word_trailing_punctuation(self):
        self.assertEqual(self.q("testers."), [])  # nothing inside the word
        self.assertEqual(self.q("user@"), [])
        self.assertEqual(self.q("site."), [])
        self.assertEqual(self.q("3.14."), ["num"])  # already contains punctuation
        self.assertEqual(self.q("user@example."), ["url"])
        self.assertEqual(self.q("alpha-"), ["hy"])  # dangling connector is fine
        self.assertEqual(self.q("maribel'"), ["apo"])

    def test_later_word_punctuation_ignored(self):
        self.assertEqual(self.q("sent on friday."), ["hy"])

    def test_leading_punctuation_is_literal(self):
        self.assertEqual(self.q("#proj"), ["tag"])
        self.assertEqual(self.q("#note"), [])
        self.assertEqual(self.q("@person"), ["tag"])

    def test_apostrophes(self):
        self.assertEqual(self.q("o'brien"), [])  # elision splits zxo'brien
        self.assertEqual(self.q("brien"), ["apo"])
        self.assertEqual(self.q("rock'n"), ["apo"])
        self.assertEqual(self.q("can t"), [])  # contractions stay whole

    def test_folding(self):
        self.assertEqual(self.q("strasse"), ["acc"])
        self.assertEqual(self.q("creme brulee"), ["acc"])
        self.assertEqual(self.q("financial"), [])  # ligatures are not folded
        self.assertEqual(self.q("ﬁnancial"), ["lig"])

    def test_symbols(self):
        self.assertEqual(self.q("🎉"), ["emo"])

    def test_compound_tails_and_camel_case(self):
        docs = [doc("t", "Snippets", "zx_snake_case_value zxnode.js zxParseThing ref zx2026q3")]

        def q(text):
            return ids(engine(docs, natural_language=False).search(text))

        self.assertEqual(q("snippets case_value"), ["t"])  # tail after _
        self.assertEqual(q("snippets .js"), ["t"])  # tail with its separator
        self.assertEqual(q("snippets thing"), ["t"])  # camelCase part
        self.assertEqual(q("ref q3"), ["t"])  # piece after a digit→letter change
        self.assertEqual(q("snippets ode"), [])  # not a word start

    def test_elision_and_leading_apostrophe(self):
        docs = [doc("f", "Lab", "l'amour d'accord qu'il y'all they'll")]

        def q(text):
            return ids(engine(docs, natural_language=False).search(text))

        self.assertEqual(q("lab amour"), ["f"])
        self.assertEqual(q("'amour"), ["f"])
        self.assertEqual(q("l'amour"), [])
        self.assertEqual(q("qu'il"), ["f"])
        self.assertEqual(q("lab il"), [])


class RankingTests(unittest.TestCase):
    def test_recovered_weights(self):
        self.assertEqual(field_score(Field.TITLE, 0, ResultType.NOTE), 32)
        self.assertEqual(field_score(Field.BODY, 0, ResultType.NOTE), 16)
        self.assertEqual(field_score(Field.AUTHORS, 0, ResultType.NOTE), 8)
        self.assertEqual(field_score(Field.TITLE, 1, ResultType.NOTE), 0.5)
        self.assertEqual(field_score(Field.TITLE, 0, ResultType.ATTACHMENT), 4)
        self.assertEqual(field_score(Field.BODY, 2, ResultType.NOTE), 1 / 128)

    def test_any_token_sets_a_field_bit(self):
        docs = [doc(1, "Lisbon", "trip notes")]
        r = engine(docs).search("lisbon trip")[0]
        self.assertEqual(r.relevance_bit_field, int(Field.TITLE) | int(Field.BODY))
        self.assertEqual(r.ranking_score, 48)

    def test_time_buckets(self):
        self.assertEqual(bucket_of_time_interval(0), 2)
        self.assertEqual(bucket_of_time_interval(600), 2)
        self.assertEqual(bucket_of_time_interval(3600), 4)
        self.assertEqual(bucket_of_time_interval(86400), 8)
        self.assertEqual(bucket_of_time_interval(7 * 86400), 10)
        self.assertEqual(bucket_of_time_interval(365 * 86400), 16)
        self.assertEqual(
            bucket_of_time_interval(86400), math.trunc(math.log(86400) * 1.28617347 - 6.14189718)
        )

    def test_default_strategy_exact_title_then_recency_shadow(self):
        docs = [
            doc(1, "Notes about taxes", "taxes taxes", days_old=1),
            doc(2, "Taxes", days_old=30),
            doc(3, "Old tax stuff", "taxes", days_old=60),
        ]
        self.assertEqual(ids(engine(docs).search("taxes")), ["2", "1", "3"])

    def test_score_strategy(self):
        docs = [doc(1, "Misc", "lisbon", days_old=0), doc(2, "Lisbon", "lisbon", days_old=9)]
        r = engine(docs, strategy=Strategy.SCORE).search("lisbon")
        self.assertEqual(ids(r), ["2", "1"])

    def test_top_hits_are_first_two(self):
        docs = [doc(i, f"Lisbon {i}", days_old=i) for i in range(5)]
        r = engine(docs).search("lisbon")
        self.assertEqual([x.is_top_hit for x in r], [True, True, False, False, False])

    def test_notes_before_attachments_and_parent_notes_added(self):
        docs = [doc(1, "Paperwork", "", attachments=["receipt.pdf"])]
        r = engine(docs).search("receipt")
        self.assertEqual(ids(r), ["1", "1/att"])
        self.assertEqual(r[0].source, "parent")


class NaturalLanguageTests(unittest.TestCase):
    def test_strips_date_phrases(self):
        text, f = nl.parse("budget last week", now=NOW)
        self.assertEqual(text, "budget")
        self.assertIsNotNone(f.modified_after)

    def test_folder_and_tags(self):
        text, f = nl.parse('ideas folder:"Work stuff" #todo')
        self.assertEqual((text, f.folder, f.tags), ("ideas", "Work stuff", ["todo"]))

    def test_nl_query_is_an_additional_pass(self):
        docs = [doc(1, "Budget", days_old=2), doc(2, "Budget 2020", days_old=400)]
        self.assertEqual(ids(engine(docs).search("budget this month")), ["1"])


class CLITests(unittest.TestCase):
    def test_unmatchable_first_word_with_other_passes(self):
        # Regression: a first word that can't match is None; highlighting must skip it.
        import contextlib
        import io

        from notes_search import cli

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            examples = Path(__file__).resolve().parents[1] / "examples" / "notes"
            cli.main(["search", "milk. bread", "--fuzzy", "--source", str(examples)])
        self.assertIn("Shopping list", out.getvalue())


class SourceTests(unittest.TestCase):
    def test_apple_notes_export_includes_recently_deleted_like_notes(self):
        import json
        import subprocess
        from unittest import mock

        from notes_search import sources

        exported = [
            {"id": "1", "title": "Kept", "folder": "Notes", "html": "<div>Kept</div><div>body</div>"},
            {"id": "2", "title": "Gone", "folder": "Recently Deleted", "html": "<div>Gone</div>"},
            {"id": "3", "title": "Weg", "folder": "Zuletzt gelöscht", "html": "<div>Weg</div>"},
        ]
        done = subprocess.CompletedProcess([], 0, stdout=json.dumps(exported), stderr="")
        with mock.patch.object(sources.subprocess, "run", return_value=done):
            all_titles = [d.title for d in sources.from_apple_notes()]
            kept = [d.title for d in sources.from_apple_notes(include_deleted=False)]
        self.assertEqual(all_titles, ["Kept", "Gone", "Weg"])
        self.assertEqual(kept, ["Kept"])

    def test_index_flags_choose_whether_deleted_notes_are_kept(self):
        import contextlib
        import io
        from unittest import mock

        from notes_search import cli

        for flags, expected in (([], True), (["--skip-deleted"], False), (["--include-deleted"], True)):
            with (
                mock.patch.object(cli, "load", return_value=[]) as load,
                mock.patch.object(cli, "_save"),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                cli.main(["index", "apple-notes", *flags])
            self.assertEqual(load.call_args.kwargs["include_deleted"], expected, flags)

    def test_html_to_text(self):
        self.assertEqual(html_to_text("<div>Title</div><div>a<br>b</div>"), "Title\na\nb")

    def test_text_flags(self):
        self.assertEqual(text_flags("- [x] done #home", ["a.pdf"]), ["attachments", "checklists", "tags"])


def _spell_service_works() -> bool:
    if not macos.available():
        return False
    return bool(macos.language_services(spell=("recpie",))["guesses"].get("recpie"))


@unittest.skipUnless(_spell_service_works(), "needs the macOS spell checker (osascript)")
class MacOSIntegrationTests(unittest.TestCase):
    def test_opt_in_spell_expansion_finds_misspelled_words(self):
        docs = [doc(1, "Pancake recipe", "flour eggs")]
        self.assertEqual(ids(SearchEngine(docs, spell_expansion=True).search("recpie")), ["1"])

    def test_correct_words_not_expanded(self):
        groups, _ = SearchEngine([], spell_expansion=True)._expand("house")
        self.assertEqual(groups, [["house"]])


if __name__ == "__main__":
    unittest.main()
