import json
import re
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_wordlist  # noqa: E402


def row(group, pos, word):
    return (group, pos, word, "", "", "", "")


class OrderTests(unittest.TestCase):
    def test_family_moves_to_its_first_member_and_duplicates_drop(self):
        rows = [row(1, 1, "confound"), row(1, 2, "abate"), row(2, 1, "confounded"), row(2, 2, "abate")]
        families = {w: ("confound", "confounded") for w in ("confound", "confounded")}
        result = build_wordlist.ordered(rows, families)
        self.assertEqual([[r[2] for r in f] for f in result], [["confound", "confounded"], ["abate"]])
        self.assertEqual(result[1][0][:2], (1, 2))

    def test_groups_never_split_a_family(self):
        families = [[row(1, i, f"w{i}")] for i in range(29)] + [[row(2, 1, "a"), row(2, 2, "b")]]
        groups = build_wordlist.chunks(families)
        self.assertEqual([sum(map(len, g)) for g in groups], [29, 2])


class RelationTests(unittest.TestCase):
    def test_lesson_words_link_when_listed_and_repeat_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = sqlite3.connect(Path(tmp) / "wordroom.sqlite3")
            db.executescript(
                "CREATE TABLE words(id INTEGER PRIMARY KEY, word TEXT, source_json TEXT);"
                "CREATE TABLE lessons(word_id INTEGER PRIMARY KEY, content TEXT);"
            )
            lesson = {"synonyms": ["perplex", "baffle"], "antonyms": ["clarify"],
                      "confusions": [{"word": "perplex", "distinction": ""}, {"word": "confuse", "distinction": ""}]}
            db.execute("INSERT INTO words VALUES (1, 'confound', '{}')")
            db.execute("INSERT INTO lessons VALUES (1, ?)", (json.dumps(lesson),))
            db.commit()
            db.close()
            relations = build_wordlist.load_relations(Path(tmp))
        html = build_wordlist.relation_list(relations, "Confound", {"perplex": "w-3-4"})
        self.assertIn('<a href="#w-3-4">perplex</a>', html)
        self.assertIn("<span>baffle</span>", html)
        self.assertEqual(html.count("perplex"), 1)
        self.assertIn("<span>confuse</span>", html)

    def test_generated_lists_fill_words_without_lessons(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "wordlist_relations.json").write_text(json.dumps(
                {"abate": {"synonyms": ["subside"], "antonyms": ["intensify"], "related": []}}))
            relations = build_wordlist.load_relations(Path(tmp))
        html = build_wordlist.relation_list(relations, "abate", {})
        self.assertIn("<span>subside</span>", html)
        self.assertNotIn("Related", html)

    def test_family_words_come_first_and_are_not_repeated(self):
        relations = {"controversial": {"Synonyms": ["contentious"], "Antonyms": ["uncontroversial"], "Related": []}}
        ids = {"controversy": "w-1-1", "uncontroversial": "w-1-4"}
        html = build_wordlist.relation_list(relations, "controversial", ids,
                                            ("controversy", "controversial", "uncontroversial"))
        self.assertLess(html.index("Family"), html.index("Synonyms"))
        self.assertIn('<a href="#w-1-1">controversy</a>', html)
        self.assertEqual(html.count("uncontroversial"), 1)
        self.assertNotIn("Antonyms", html)
        self.assertEqual(html.count(">controversial<"), 0)

    def test_missing_lessons_database_means_no_relations(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(build_wordlist.load_relations(Path(tmp)), {})


class SentenceTests(unittest.TestCase):
    def test_marks_the_first_whole_word_use_and_escapes(self):
        html = build_wordlist.marked("Abatement <of> the storm did not abate; it would abate later.", "abate")
        self.assertEqual(html, "Abatement &lt;of&gt; the storm did not <em>abate</em>; it would abate later.")

    def test_marks_a_phrase_across_spacing(self):
        html = build_wordlist.marked("The decline was attributed  to drought.", "attributed to")
        self.assertIn("<em>attributed  to</em>", html)

    def test_generated_sentences_load_by_word(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "wordlist_sentences.json").write_text(json.dumps({"abate": {"en": "E", "zh": "Z"}}))
            self.assertEqual(build_wordlist.load_sentences(Path(tmp)), {"abate": {"en": "E", "zh": "Z"}})
            self.assertEqual(build_wordlist.load_sentences(Path(tmp) / "missing"), {})


    def test_structure_spans_keep_the_text_and_mark_the_word(self):
        parts = [["Far from <trivial>", "phrase"], [", ", "other"], ["the notes", "core"],
                 [" that readers abate", "clause"], [".", "other"]]
        html = build_wordlist.structured(parts, "abate")
        self.assertEqual(html, '<span class="s-phrase">Far from &lt;trivial&gt;</span>, <span class="s-core">the notes</span>'
                               ' <span class="s-clause">that readers <em>abate</em></span>.')

    def test_breakdown_needs_parts_and_skeleton(self):
        self.assertEqual(build_wordlist.breakdown({"en": "x", "zh": "y"}), "")
        html = build_wordlist.breakdown({"parts": [["x", "core"]], "skeleton": "Notes abate.", "note": "主句"})
        self.assertIn('<p class="skeleton" lang="en">Notes abate.</p>', html)
        self.assertIn("主句", html)


class RenderTests(unittest.TestCase):
    def test_pair_choice_is_optional_and_keeps_phrases_and_distinct_partners(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            links, sections = [], []
            self.assertEqual(build_wordlist.render_pairs(directory, links, sections), "")
            pairs = [
                {"words": ["turn up", partner], "glosses": ["出现", "到达"],
                 "meaning": "义项 <test>", "pages": [4, 7]}
                for partner in ["arrive", "appear"]
            ]
            path = directory / "wordlist_pairs.private.json"
            path.write_text(json.dumps(pairs))
            choice = build_wordlist.render_pairs(directory, links, sections)
            self.assertIn('data-set="pairs">2', choice)
            self.assertEqual(len(links), 1)
            self.assertEqual(sections[0].count('class="word pair"'), 2)
            self.assertIn("义项 &lt;test&gt;", sections[0])
            self.assertIn("第 4、7 页", sections[0])
            ids = re.findall(r'id="(p-[a-f0-9]+)"', sections[0])
            self.assertEqual(len(set(ids)), 2)
            path.write_text(json.dumps(pairs[::-1]))
            reordered = []
            build_wordlist.render_pairs(directory, [], reordered)
            self.assertEqual(re.findall(r'id="(p-[a-f0-9]+)"', reordered[0]), ids[::-1])

    def test_page_has_closed_rows_only_and_details_carry_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "bb.sqlite3"
            db = sqlite3.connect(source)
            db.execute("CREATE TABLE words(group_number, position, word, phonetic, meaning_zh, example_en, example_zh)")
            db.execute("INSERT INTO words VALUES (1, 1, 'abate', '/ə/', '减弱', 'The storm did abate.', '风暴减弱了。')")
            db.commit(); db.close()
            (Path(tmp) / "wordlist_sentences.json").write_text(json.dumps({"abate": {
                "en": "Storms abate.", "zh": "风暴减弱。", "parts": [["Storms abate", "core"], [".", "other"]],
                "skeleton": "Storms abate.", "note": "主句"}}))
            html, details = build_wordlist.render(source, Path(tmp))
        self.assertIn('<span class="sentence" lang="en">Storms <em>abate</em>.</span>', html)
        self.assertIn('<div class="example"></div>', html)
        self.assertNotIn("风暴减弱。", html)
        sentence, detail = details["w-1-1"]
        self.assertIn('<span class="s-core">Storms <em>abate</em></span>', sentence)
        self.assertIn("风暴减弱。", detail)
        self.assertIn('class="structure"', detail)
        self.assertIn('<script type="application/json" id="wordlist-details">',
                      build_wordlist.standalone(html, {"w-1-1": ["</script>", ""]}))
        self.assertNotIn('["</script>"', build_wordlist.standalone(html, {"w-1-1": ["</script>", ""]}))


class StandaloneTests(unittest.TestCase):
    def page(self):
        from string import Template
        template = Template((build_wordlist.ROOT / "templates/wordlist.html").read_text(encoding="utf-8"))
        return template.substitute(pair_choice="", group_links="", word_groups="", total="0")

    def test_without_an_online_url_there_is_no_online_link(self):
        html = build_wordlist.standalone(self.page(), {})
        self.assertNotIn('id="online"', html)
        self.assertNotIn('src="/', html)

    def test_online_url_sets_the_online_link(self):
        html = build_wordlist.standalone(self.page(), {}, "https://words.example.test")
        self.assertIn('<a id="online" href="https://words.example.test/"', html)


if __name__ == "__main__":
    unittest.main()
