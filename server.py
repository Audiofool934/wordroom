#!/usr/bin/env python3
"""Wordroom: serves the GRE word list and keeps its scores. Python 3.11+, standard library only."""
from email.utils import formatdate, parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import argparse
import gzip
import json
import mimetypes
import secrets
import signal
import threading

from domain import AppError
from store import Store

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
# Built into the data directory by scripts/build_wordlist.py.
GENERATED = {"/": "wordlist.html", "/wordlist": "wordlist.html", "/wordlist-details.json": "wordlist-details.json"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


class Application:
    def __init__(self, data_dir, public_origin=None, tailscale_user=None):
        self.public_origin = public_origin
        self.tailscale_user = tailscale_user
        self.store = Store(data_dir)
        # On loopback, writes carry a per-run token; behind Tailscale Serve the identity header is checked instead.
        self.token = None if tailscale_user else secrets.token_urlsafe(32)
        # Compressed copies of static files, keyed by path, mtime and size, so each version is compressed once.
        self.compressed = {}
        self.compressed_lock = threading.Lock()

    def gzipped(self, target, stat):
        key = (str(target), stat.st_mtime_ns, stat.st_size)
        with self.compressed_lock:
            cached = self.compressed.get(str(target))
        if cached and cached[0] == key:
            return cached[1]
        raw = gzip.compress(target.read_bytes(), compresslevel=6, mtime=0)
        with self.compressed_lock:
            self.compressed[str(target)] = (key, raw)
        return raw


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    server_version = "Wordroom"

    @property
    def app(self):
        return self.server.app

    @staticmethod
    def accepts_gzip(item):
        """True for "gzip" or "gzip;q=0.5", false for "gzip;q=0"."""
        name, _, params = item.partition(";")
        if name.strip().lower() != "gzip":
            return False
        for param in params.split(";"):
            key, _, value = param.partition("=")
            if key.strip().lower() == "q":
                try:
                    return float(value) > 0
                except ValueError:
                    return False
        return True

    def not_modified(self, etag, mtime):
        match = self.headers.get("If-None-Match")
        if match is not None:
            return etag in [tag.strip() for tag in match.split(",")]
        since = self.headers.get("If-Modified-Since")
        if not since:
            return False
        try:
            return mtime <= parsedate_to_datetime(since).timestamp()
        except (TypeError, ValueError, IndexError, OverflowError):
            return False

    def log_message(self, fmt, *args):
        # Keep request details out of logs.
        return

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def end_headers(self):
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        super().end_headers()

    def reply(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def check_request(self, mutation=False):
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if self.app.public_origin:
            allowed = {urlsplit(self.app.public_origin).netloc}
        if host not in allowed:
            raise AppError("Invalid host.", 403)
        origin = self.headers.get("Origin")
        origins = {self.app.public_origin} if self.app.public_origin else {"http://" + h for h in allowed}
        if origin and origin not in origins:
            raise AppError("Invalid origin.", 403)
        if self.app.tailscale_user:
            identities = self.headers.get_all("Tailscale-User-Login", [])
            if len(identities) != 1 or identities[0].casefold() != self.app.tailscale_user.casefold():
                raise AppError("Open Wordroom from your own Tailscale device.", 403)
            if mutation and origin != self.app.public_origin:
                raise AppError("Invalid origin.", 403)
            return
        if mutation and not secrets.compare_digest(self.headers.get("X-Wordroom-Token", ""), self.app.token):
            raise AppError("This page is out of date. Reload and try again.", 403)

    def static_target(self, path):
        built = self.app.store.directory / GENERATED[path] if path in GENERATED else None
        if built and built.is_file():
            return built
        if path == "/wordlist-details.json":
            raise AppError("Not found.", 404)
        relative = "setup.html" if path in GENERATED else path.lstrip("/")
        target = (PUBLIC / relative).resolve()
        if not target.is_relative_to(PUBLIC.resolve()) or not target.is_file():
            raise AppError("Not found.", 404)
        return target

    def send_static(self, target):
        stat = target.stat()
        # Browsers keep a copy and ask again each time; an unchanged file costs a 304 with no body.
        etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
        cache_control = "private, no-cache"
        # Some browsers ignore an ETag from an HTTP/1.0 server, so the modification date is a second check.
        modified = formatdate(stat.st_mtime, usegmt=True)
        if self.not_modified(etag, int(stat.st_mtime)):
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Last-Modified", modified)
            self.send_header("Cache-Control", cache_control)
            self.send_header("Vary", "Accept-Encoding")
            self.end_headers()
            return
        content_type = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if target.suffix == ".webmanifest":
            content_type = "application/manifest+json"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/manifest+json", "image/svg+xml"}:
            content_type += "; charset=utf-8"
        encodings = self.headers.get("Accept-Encoding", "").lower().split(",")
        textual = content_type.split(";")[0] in {"text/html", "text/css", "text/javascript", "application/javascript",
                                                 "application/json", "image/svg+xml", "application/manifest+json"}
        compress = textual and any(self.accepts_gzip(item) for item in encodings)
        raw = self.app.gzipped(target, stat) if compress else target.read_bytes()
        self.send_response(200)
        self.send_header("Vary", "Accept-Encoding")
        if compress:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", modified)
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        try:
            self.check_request()
            path = urlsplit(self.path).path
            if path == "/api/wordlist/scores":
                state = self.app.store.wordlist_state()
                if self.app.token:
                    state["token"] = self.app.token
                return self.reply(state)
            if path.startswith("/api/"):
                raise AppError("Not found.", 404)
            self.send_static(self.static_target(path))
        except AppError as exc:
            self.reply({"error": str(exc)}, exc.status)
        except (ValueError, TypeError):
            self.reply({"error": "Invalid request."}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.reply({"error": "Could not load that. Try again."}, 500)

    def do_POST(self):
        try:
            path = urlsplit(self.path).path
            self.check_request(True)
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise AppError("Send JSON.", 415)
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 20_000:
                raise AppError("Invalid request size.", 413)
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise AppError("Invalid request.")
            if path == "/api/wordlist/scores":
                return self.reply(self.app.store.mark_wordlist(payload))
            if path == "/api/wordlist/position":
                return self.reply({"position": self.app.store.save_wordlist_position(payload)})
            raise AppError("Not found.", 404)
        except AppError as exc:
            self.reply({"error": str(exc)}, exc.status)
        except (ValueError, TypeError):
            self.reply({"error": "Invalid request."}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.reply({"error": "That was not saved. Try again."}, 500)


def make_server(port=8768, data_dir=None, public_origin=None, tailscale_user=None, host="127.0.0.1"):
    """A loopback server. Other devices reach it only through tailscale serve, which proves who they are."""
    if host not in LOOPBACK:
        raise ValueError("Wordroom listens on loopback only; use tailscale serve for other devices.")
    if public_origin:
        url = urlsplit(public_origin)
        if url.scheme != "https" or not url.netloc or url.path or url.query or url.fragment or url.username or url.password:
            raise ValueError("Public origin must be an HTTPS origin without a path.")
    if bool(public_origin) != bool(tailscale_user) or (tailscale_user is not None and not tailscale_user.strip()):
        raise ValueError("--public-origin and --tailscale-user go together.")
    server = Server((host, port), Handler)
    server.app = Application(data_dir or ROOT / ".runtime", public_origin, tailscale_user)
    return server


def main():
    parser = argparse.ArgumentParser(description="Start Wordroom on this machine")
    parser.add_argument("--host", default="127.0.0.1", choices=sorted(LOOPBACK), help="A loopback address")
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--data-dir", type=Path, default=ROOT / ".runtime")
    parser.add_argument("--public-origin", help="The tailscale serve HTTPS origin, such as https://host.tailnet.ts.net")
    parser.add_argument("--tailscale-user", help="The Tailscale login allowed through tailscale serve")
    parser.add_argument("--open", action="store_true", help="Open the list in your default browser")
    args = parser.parse_args()
    server = make_server(args.port, args.data_dir, args.public_origin, args.tailscale_user, args.host)
    signal.signal(signal.SIGTERM, lambda signum, frame: threading.Thread(target=server.shutdown, daemon=True).start())
    signal.signal(signal.SIGINT, lambda signum, frame: threading.Thread(target=server.shutdown, daemon=True).start())
    print(f"Wordroom: http://127.0.0.1:{server.server_port}", flush=True)
    if args.open:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{server.server_port}")
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
