from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import gzip
import json
import stat
import sys
import tempfile
import threading
import unittest
import unittest.mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from domain import AppError  # noqa: E402
from providers import ProviderSettings, request_json  # noqa: E402
from server import make_server  # noqa: E402

POSITION = {"row": "w-1-1", "view": "active", "query": "", "expanded": False}


class ServerFixture:
    origin = None
    user = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = make_server(0, self.tmp.name, self.origin, self.user)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def status(self, path, payload=None, headers=None):
        req = Request(self.base + path, data=json.dumps(payload).encode() if payload is not None else None,
                      headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urlopen(req) as response:
                return response.status
        except HTTPError as exc:
            exc.close()
            return exc.code


class LoopbackCase(ServerFixture, unittest.TestCase):
    def request(self, path, payload=None, headers=None):
        h = {"Content-Type": "application/json", "X-Wordroom-Token": self.server.app.token, **(headers or {})}
        req = Request(self.base + path, data=json.dumps(payload).encode() if payload is not None else None, headers=h)
        with urlopen(req) as response:
            return json.load(response)

    def test_scores_round_trip_with_the_page_token(self):
        state = self.request("/api/wordlist/scores")
        self.assertEqual(state["token"], self.server.app.token)
        saved = self.request("/api/wordlist/scores", {"word": "obscure", "action": "plus", "request_id": "r1", "position": POSITION})
        self.assertEqual((saved["score"], saved["position"]["row"]), (1, "w-1-1"))
        self.assertEqual(self.request("/api/wordlist/scores")["scores"]["obscure"][0], 1)
        place = self.request("/api/wordlist/position", {**POSITION, "row": "w-2-3", "base": saved["position"]["revision"]})
        self.assertEqual(place["position"]["row"], "w-2-3")

    def test_rejects_cross_origin_missing_token_and_wrong_host(self):
        mark = {"word": "obscure", "action": "plus", "request_id": "r2"}
        for headers in ({"Origin": "https://evil.example", "X-Wordroom-Token": self.server.app.token},
                        {"X-Wordroom-Token": ""}, {"Host": "evil.example", "X-Wordroom-Token": self.server.app.token}):
            self.assertEqual(self.status("/api/wordlist/scores", mark, headers), 403)
        self.assertEqual(self.request("/api/wordlist/scores")["scores"], {})

    def test_unknown_api_is_not_found(self):
        self.assertEqual(self.status("/api/bootstrap"), 404)
        self.assertEqual(self.status("/api/review", {"word_id": 1}, {"X-Wordroom-Token": self.server.app.token}), 404)

    def test_setup_page_until_the_list_is_built(self):
        with urlopen(self.base + "/") as response:
            self.assertIn("has not been built yet", response.read().decode())
        (Path(self.tmp.name) / "wordlist.html").write_text("<!doctype html><p>word</p>")
        with urlopen(self.base + "/") as response:
            self.assertIn("<p>word</p>", response.read().decode())

    def test_word_list_files_revalidate_and_compress_once(self):
        page = Path(self.tmp.name) / "wordlist.html"
        page.write_text("<!doctype html>" + "<p>word</p>" * 400)
        (Path(self.tmp.name) / "wordlist-details.json").write_text('{"w-1-1":["",""]}')
        with urlopen(Request(self.base + "/", headers={"Accept-Encoding": "gzip"})) as response:
            etag = response.headers["ETag"]
            self.assertEqual(response.headers["Content-Encoding"], "gzip")
            self.assertEqual(response.headers["Cache-Control"], "private, no-cache")
            self.assertIn("<p>word</p>", gzip.decompress(response.read()).decode())
        self.assertEqual(self.status("/", headers={"If-None-Match": etag}), 304)
        self.assertEqual(len(self.server.app.compressed), 1)
        with urlopen(self.base + "/wordlist-details.json") as response:
            self.assertEqual(json.load(response), {"w-1-1": ["", ""]})
        page.write_text("<!doctype html><p>changed</p>")
        with urlopen(Request(self.base + "/", headers={"If-None-Match": etag})) as response:
            self.assertNotEqual(response.headers["ETag"], etag)
            self.assertIn("changed", response.read().decode())

    def test_gzip_with_zero_quality_is_refused(self):
        (Path(self.tmp.name) / "wordlist.html").write_text("<!doctype html><p>word</p>")
        with urlopen(Request(self.base + "/", headers={"Accept-Encoding": "gzip;q=0"})) as response:
            self.assertIsNone(response.headers["Content-Encoding"])

    def test_word_list_revalidates_by_date_without_an_etag(self):
        (Path(self.tmp.name) / "wordlist.html").write_text("<!doctype html><p>word</p>")
        with urlopen(self.base + "/") as response:
            modified = response.headers["Last-Modified"]
        self.assertEqual(self.status("/", headers={"If-Modified-Since": modified}), 304)
        self.assertEqual(self.status("/", headers={"If-Modified-Since": "Thu, 01 Jan 2015 00:00:00 GMT"}), 200)

    def test_missing_details_and_traversal_are_not_found(self):
        self.assertEqual(self.status("/wordlist-details.json"), 404)
        self.assertEqual(self.status("/../server.py"), 404)

    def test_installable_web_app_manifest_and_icons(self):
        with urlopen(self.base + "/manifest.webmanifest") as response:
            self.assertEqual(response.headers["Content-Type"], "application/manifest+json; charset=utf-8")
            manifest = json.load(response)
        self.assertEqual((manifest["display"], manifest["start_url"]), ("standalone", "/"))
        for src in [icon["src"] for icon in manifest["icons"]] + ["/icons/apple-touch-icon.png"]:
            with urlopen(self.base + src) as response:
                self.assertTrue(response.read().startswith(b"\x89PNG\r\n\x1a\n"))

    def test_only_loopback_and_paired_tailscale_options_start(self):
        for args in ((0, self.tmp.name, None, None, "0.0.0.0"), (0, self.tmp.name, "https://w.example.ts.net", None),
                     (0, self.tmp.name, None, "me@github"), (0, self.tmp.name, "http://w.example.ts.net", "me@github")):
            with self.assertRaises(ValueError):
                make_server(*args)


class TailscaleCase(ServerFixture, unittest.TestCase):
    origin = "https://words.example.ts.net"
    user = "Reader@github"

    def headers(self, **extra):
        return {"Host": "words.example.ts.net", "Tailscale-User-Login": "reader@github", **extra}

    def test_the_named_user_reads_and_writes(self):
        self.assertEqual(self.status("/api/wordlist/scores", headers=self.headers()), 200)
        mark = {"word": "obscure", "action": "plus", "request_id": "t1"}
        self.assertEqual(self.status("/api/wordlist/scores", mark, self.headers(Origin=self.origin)), 200)

    def test_other_users_missing_identity_and_foreign_origin_are_refused(self):
        self.assertEqual(self.status("/api/wordlist/scores", headers=self.headers(**{"Tailscale-User-Login": "other@github"})), 403)
        self.assertEqual(self.status("/api/wordlist/scores", headers={"Host": "words.example.ts.net"}), 403)
        mark = {"word": "obscure", "action": "plus", "request_id": "t2"}
        self.assertEqual(self.status("/api/wordlist/scores", mark, self.headers()), 403)
        self.assertEqual(self.status("/api/wordlist/scores", mark, self.headers(Origin="https://evil.example")), 403)


class ProviderSettingsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = ProviderSettings(self.tmp.name)
        self.settings.save({"provider": "custom", "base_url": "https://example.com/v1", "model": "m", "api_key": "test-secret"})

    def tearDown(self):
        self.tmp.cleanup()

    def test_public_settings_hide_keys_and_file_is_private(self):
        self.assertNotIn("test-secret", json.dumps(self.settings.public()))
        self.assertEqual(stat.S_IMODE(self.settings.path.stat().st_mode), 0o600)

    def test_changed_endpoint_does_not_inherit_key(self):
        self.settings.save({"provider": "custom", "base_url": "https://other.example.com/v1", "model": "m", "api_key": ""})
        self.assertFalse(self.settings.public()["providers"]["custom"]["has_key"])

    def test_invalid_remote_url_rejected(self):
        for url in ("http://example.com", "https://user:password@example.com",
                    "https://example.com/v1/chat/completions", "https://example.com?api_key=x"):
            with self.assertRaises(AppError):
                self.settings.validate_url(url)


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.calls.append((self.headers.get("Authorization"), body))
        if len(self.server.calls) == 1:  # The first call fails, so the helper has to retry.
            self.send_response(503)
            self.end_headers()
            return
        reply = {"choices": [{"message": {"content": '```json\n{"entries": []}\n```'}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 3, "completion_tokens": 4}}
        raw = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class RequestJsonCase(unittest.TestCase):
    def test_retries_a_server_error_and_parses_fenced_json(self):
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        upstream.calls = []
        thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        try:
            config = {"base_url": f"http://127.0.0.1:{upstream.server_port}/v1", "model": "m", "api_key": "k", "json_mode": True}
            with unittest.mock.patch("providers.time.sleep"):
                parsed, usage = request_json("custom", config, "system", {"entries": []})
        finally:
            upstream.shutdown()
            upstream.server_close()
        self.assertEqual(parsed, {"entries": []})
        self.assertEqual(usage, {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 0})
        self.assertEqual(len(upstream.calls), 2)
        self.assertEqual(upstream.calls[1][0], "Bearer k")
        self.assertEqual(upstream.calls[1][1]["response_format"], {"type": "json_object"})


if __name__ == "__main__":
    unittest.main()
