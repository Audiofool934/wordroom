from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from uuid import uuid4

from domain import AppError
from store import Store

ROOT = Path(__file__).resolve().parents[1]


class WordlistMarksTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(temp.name)

    def test_older_database_tables_are_left_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            with closing(sqlite3.connect(Path(tmp) / "wordroom.sqlite3")) as db, db:
                db.execute("CREATE TABLE lessons(word_id INTEGER PRIMARY KEY, content TEXT)")
                db.execute("INSERT INTO lessons VALUES (1, '{}')")
            Store(tmp)
            with closing(sqlite3.connect(Path(tmp) / "wordroom.sqlite3")) as db:
                self.assertEqual(db.execute("SELECT * FROM lessons").fetchall(), [(1, "{}")])

    def mark(self, action, **params):
        return self.store.mark_wordlist({"word": "obscure", "action": action, "request_id": str(uuid4()), **params})

    def test_retries_and_simultaneous_increments(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.mark("plus"), range(8)))
        req = str(uuid4())
        first = self.mark("minus", request_id=req)
        self.mark("plus")
        replay = self.mark("minus", request_id=req)
        self.assertEqual(first["revision"], replay["revision"])
        self.assertEqual(self.store.wordlist_scores()["obscure"][0], 8)

    def test_archive_undo_restore_and_stale_undo(self):
        self.mark("plus")
        self.mark("plus")
        archive = self.mark("archive")
        self.assertEqual(self.mark("undo", undo_revision=archive["revision"])["score"], 2)
        self.mark("archive")
        with self.assertRaises(AppError):
            self.mark("undo", undo_revision=archive["revision"])
        self.assertEqual(self.mark("restore")["score"], 0)
        self.assertEqual(self.mark("minus")["score"], -1)
        with self.assertRaises(AppError):
            self.mark("minus")
        with closing(sqlite3.connect(self.store.directory / "backups" / self.store.backup())) as db:
            self.assertEqual(db.execute("SELECT score FROM wordlist_marks ORDER BY revision DESC LIMIT 1").fetchone()[0], -1)

    def test_position_order_archive_and_atomic_retry(self):
        old = self.store.save_wordlist_position({"row": "w-1-6", "base": 0})
        action = self.mark("archive", request_id="archive-once", position={"row": "w-35-8", "query": "obs"})
        latest = action["position"]
        self.assertEqual((latest["row"], latest["view"], latest["query"]), ("w-35-8", "archive", "obs"))
        stale = self.store.save_wordlist_position({"row": "w-1-7", "base": old["revision"]})
        self.assertEqual(stale, latest)
        moved = self.store.save_wordlist_position({"row": "w-2-5", "base": latest["revision"], "expanded": True})
        replay = self.mark("archive", request_id="archive-once", position={"row": "w-35-8"})
        self.assertEqual(replay["position"], moved)
        self.assertEqual(self.store.wordlist_state()["position"], moved)
        self.assertEqual(self.mark("restore", position={"row": "w-35-8", "view": "archive"})["position"]["view"], "active")
        before = self.store.wordlist_state()
        with self.assertRaises(AppError):
            self.mark("plus", position={"row": "bad"})
        self.assertEqual(self.store.wordlist_state(), before)

    def test_marked_word_takes_the_place_to_review_and_back(self):
        row = {"row": "w-35-8", "view": "active"}
        self.assertEqual(self.mark("plus", position=row)["position"]["view"], "review")
        self.assertEqual(self.mark("plus", position={**row, "view": "review"})["position"]["view"], "review")
        self.assertEqual(self.mark("minus", position={**row, "view": "review"})["position"]["view"], "review")
        self.assertEqual(self.mark("minus", position={**row, "view": "review"})["position"]["view"], "active")

    def test_legacy_last_mark_for_review(self):
        self.mark("plus")
        self.assertEqual(self.store.wordlist_state()["position"]["view"], "review")

    def test_collection_persists_through_scoring_and_can_be_cleared(self):
        row = {"row": "w-1-4", "collection": "reading"}
        saved = self.store.save_wordlist_position({**row, "base": 0})
        self.assertEqual(saved["collection"], "reading")
        marked = self.mark("minus", position=row)["position"]
        self.assertEqual((marked["collection"], marked["view"]), ("reading", "archive"))
        with self.assertRaises(AppError):
            self.store.save_wordlist_position({**row, "collection": "unknown", "base": marked["revision"]})
        self.assertEqual(self.store.wordlist_state()["position"], marked)
        cleared = self.store.save_wordlist_position({"row": "w-1-4", "base": marked["revision"]})
        self.assertEqual(cleared.get("collection", "all"), "all")

    def test_completion_question_bookmark_is_distinct_from_choices(self):
        row = {"row": "w-1-4", "base": 0, "collection": "fill"}
        saved = self.store.save_wordlist_position(row)
        self.assertEqual(self.store.wordlist_state()["position"]["collection"], "fill")
        choices = self.store.save_wordlist_position({**row, "base": saved["revision"], "collection": "completion"})
        self.assertEqual(choices["collection"], "completion")

    def test_legacy_last_mark(self):
        self.mark("minus")
        self.assertEqual(self.store.wordlist_state()["position"], {"word": "obscure", "view": "archive", "revision": 0})
