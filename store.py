"""SQLite persistence for the word list: scores by word and the saved reading place."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import re
import sqlite3

from domain import AppError, text


class Store:
    """Owns wordroom.sqlite3 in the data directory.

    Only the word list's own tables are created. A database from an older Wordroom
    may hold other tables (such as study-room lessons); they are left untouched.
    """

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "wordroom.sqlite3"
        with self.connection() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS wordlist_marks(
                    revision INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                    word TEXT NOT NULL, action TEXT NOT NULL, undo_revision INTEGER NOT NULL,
                    previous INTEGER NOT NULL, score INTEGER NOT NULL CHECK(score >= -1)
                );
                CREATE INDEX IF NOT EXISTS wordlist_marks_word ON wordlist_marks(word, revision);
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _wordlist_position(db):
        row = db.execute("SELECT value FROM settings WHERE key='wordlist_position'").fetchone()
        return json.loads(row["value"]) if row else None

    def wordlist_state(self):
        with self.connection() as db:
            db.execute("BEGIN")
            rows = db.execute("""
                SELECT word, score, revision FROM wordlist_marks JOIN
                    (SELECT max(revision) AS revision FROM wordlist_marks GROUP BY word)
                    USING (revision)
            """)
            scores = {row["word"]: [row["score"], row["revision"]] for row in rows}
            position = self._wordlist_position(db)
            if not position:
                last = db.execute("SELECT word,score FROM wordlist_marks ORDER BY revision DESC LIMIT 1").fetchone()
                if last:
                    position = {"word": last["word"], "view": ("archive" if last["score"] < 0 else "review" if last["score"] > 0 else "active"),
                                "revision": 0}
            return {"scores": scores, "position": position}

    def wordlist_scores(self):
        return self.wordlist_state()["scores"]

    @classmethod
    def _save_wordlist_position(cls, db, payload, *, score=None):
        if not isinstance(payload, dict):
            raise AppError("Invalid reading position.")
        row = payload.get("row")
        view = payload.get("view", "active")
        collection = payload.get("collection", "all")
        query = payload.get("query", "")
        expanded = payload.get("expanded", False)
        if (not isinstance(row, str) or not re.fullmatch(r"w-[1-9][0-9]{0,4}-[1-9][0-9]{0,4}", row)
                or view not in ("active", "review", "archive") or not isinstance(query, str)
                or collection not in ("all", "super", "fill", "completion", "reading")
                or len(query) > 256 or type(expanded) is not bool):
            raise AppError("Invalid reading position.")
        current = cls._wordlist_position(db)
        revision = current["revision"] if current else 0
        if score is None:
            base = payload.get("base")
            if type(base) is not int or base < 0:
                raise AppError("Invalid reading position revision.")
            if base != revision and payload.get("direct") is not True:
                return current
        elif score == -1:
            view = "archive"
        elif view == "archive" or (view == "review" and score == 0):
            view = "active"
        elif view == "active" and score > 0:
            view = "review"
        position = {"row": row, "view": view, "query": query, "expanded": expanded, "revision": revision + 1}
        if collection != "all":
            position["collection"] = collection
        db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('wordlist_position',?)",
                   (json.dumps(position, ensure_ascii=False),))
        return position

    def save_wordlist_position(self, payload):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._save_wordlist_position(db, payload)

    def mark_wordlist(self, payload):
        word = text(payload.get("word"), "Word", 160)
        request_id = text(payload.get("request_id"), "Request id", 80)
        action = payload.get("action")
        if action not in ("plus", "minus", "archive", "restore", "undo"):
            raise AppError("Unknown word list action.")
        undo_revision = payload.get("undo_revision", 0)
        if type(undo_revision) is not int or not 0 <= undo_revision < 2**53:
            raise AppError("Invalid archive record.")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT * FROM wordlist_marks WHERE request_id=?", (request_id,)).fetchone()
            if prior:
                if (prior["word"], prior["action"], prior["undo_revision"]) != (word, action, undo_revision):
                    raise AppError("This request id was already used.", 409)
                return {**dict(prior), "position": self._wordlist_position(db)}

            last = db.execute("SELECT * FROM wordlist_marks WHERE word=? ORDER BY revision DESC LIMIT 1",
                              (word,)).fetchone()
            previous = last["score"] if last else 0
            if action == "undo":
                if previous != -1 or not last or last["revision"] != undo_revision:
                    raise AppError("This word has changed.", 409)
                score = last["previous"]
            elif action == "restore":
                if previous != -1:
                    raise AppError("This word is no longer archived.", 409)
                score = 0
            elif previous == -1:
                raise AppError("This word is archived.", 409)
            elif action == "archive":
                score = -1
            else:
                score = previous + (1 if action == "plus" else -1)

            cur = db.execute("""
                INSERT INTO wordlist_marks(request_id,word,action,undo_revision,previous,score)
                VALUES(?,?,?,?,?,?)
            """, (request_id, word, action, undo_revision, previous, score))
            position = self._save_wordlist_position(db, payload["position"], score=score) if "position" in payload else None
            return {"word": word, "score": score, "previous": previous, "revision": cur.lastrowid, "position": position}

    def backup(self):
        """Copy the whole database into <data-dir>/backups and return the file name."""
        # SQLite's backup API includes committed WAL data, unlike copying the main file.
        target = self.directory / "backups"
        target.mkdir(exist_ok=True, mode=0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = target / f"wordroom-{stamp}.sqlite3"
        with self.connection() as db:
            dest = sqlite3.connect(path)
            try:
                db.backup(dest)
            finally:
                dest.close()
        return path.name


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Back up the Wordroom database")
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    print(args.data_dir / "backups" / Store(args.data_dir).backup())


if __name__ == "__main__":
    main()
