from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prototype_trail as trail  # noqa: E402


class InputTests(unittest.TestCase):
    def test_text_list_with_optional_meanings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "words.txt"
            path.write_text("abate\t减弱\nAbate\n\nlucid\n", encoding="utf-8")
            entries = trail.read_words(path)
        self.assertEqual(list(entries), ["abate", "lucid"])
        self.assertEqual(entries["abate"]["meaning"], "减弱")

    def test_paragraphs_skip_reference_sections_and_short_lines(self):
        body = "x" * 130
        extract = f"{body}\nshort line\n== History ==\n{body} two\n== References ==\n{body} three"
        found = trail.paragraphs(extract)
        self.assertEqual([p["section"] for p in found], ["Introduction", "History"])


class CheckTests(unittest.TestCase):
    entries = {"abate": {"word": "abate", "meaning": ""}, "lucid": {"word": "lucid", "meaning": ""}}

    def test_plan_keeps_known_paragraphs_and_each_word_once(self):
        plan = trail.check_plan({"title": "T", "passages": [
            {"heading": "One", "paragraphs": ["S1.P1", "S9.P9"], "targets": ["Abate", "nope"]},
            {"heading": "Two", "paragraphs": ["S1.P1"], "targets": ["abate", "lucid"]},
            {"heading": "Three", "paragraphs": ["S9.P9"], "targets": ["lucid"]},
        ]}, self.entries, {"S1.P1": "text"})
        self.assertEqual([(p["heading"], p["paragraphs"], p["targets"]) for p in plan["passages"]],
                         [("One", ["S1.P1"], ["abate"]), ("Two", ["S1.P1"], ["lucid"])])

    def test_sentences_need_a_valid_citation_and_report_real_words(self):
        sentences = trail.check_sentences({"sentences": [
            {"text": "Storms  abate slowly.", "cites": ["S1.P1"], "words": ["abate", "lucid"]},
            {"text": "No source.", "cites": ["S7.P1"]},
        ]}, ["abate", "lucid"], {"S1.P1": "text"})
        self.assertEqual(sentences, [{"text": "Storms abate slowly.", "cites": ["S1.P1"], "words": ["abate"]}])

    def test_sentences_about_the_sources_are_rejected(self):
        sentences = trail.check_sentences({"sentences": [
            {"text": "Storms abate, though the paragraph does not say why.", "cites": ["S1.P1"]},
            {"text": "As the cited sources note, storms abate.", "cites": ["S1.P1"]},
        ]}, ["abate"], {"S1.P1": "text"})
        self.assertEqual(sentences, [])


class RenderTests(unittest.TestCase):
    def test_words_are_marked_once_and_notes_cannot_break_markup(self):
        page = trail.render({
            "title": "T", "intro": "", "meanings": {"abate": "减弱"},
            "sources": [{"title": "A", "url": "https://example.test/A", "revision": 1, "retrieved": "2026-11-26",
                         "license": "CC BY-SA 4.0", "paragraphs": [{"id": "S1.P1", "text": "</script> text"}]}],
            "passages": [{"heading": "H", "targets": ["abate", "lucid"], "missing_words": [], "dropped": [],
                          "glosses": {"abate": "to lessen, as in lucid <b>", "lucid": "clear"},
                          "sentences": [{"text": "Lucid storms abate.", "cites": ["S1.P1"], "words": ["abate", "lucid"]}]}],
        })
        self.assertEqual(page.count('class="target"'), 2)
        self.assertIn('data-note="to lessen, as in lucid &lt;b&gt; · 减弱">abate</button>', page)
        self.assertNotIn("</script> text", page)


if __name__ == "__main__":
    unittest.main()
