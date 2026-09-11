"""Exercise the HTTP client and the fetch/discover commands against a local
stand-in for vjudge.net (the real site cannot be reached from CI)."""

import io
import json
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from jollyrating.cli import main
from jollyrating.vjudge import VJudgeAuthError, VJudgeClient, VJudgeError, load_cookie

NOW_MS = int(time.time() * 1000)
ENDED = {
    "id": 1, "title": "Ended contest", "begin": NOW_MS - 10 * 86400_000, "length": 3_600_000,
    "participants": {"7": ["alice", "Alice", ""], "8": ["bob", "", ""]},
    "submissions": [[7, 0, 1, 100], [8, 0, 0, 200]],
}
RUNNING = {
    "id": 4, "title": "Running contest", "begin": NOW_MS - 60_000, "length": 3_600_000,
    "participants": {"7": ["alice", "Alice", ""]},
    "submissions": [],
}
LOGIN_HTML = "<!DOCTYPE html><html><head><title>Login - Virtual Judge</title></head><body>login</body></html>"
GROUP_HTML = """<!DOCTYPE html><html><body>
<script>var groupId = 77;</script>
<table><tr><td><a href="/contest/1">Ended contest</a></td></tr>
<tr><td><a href="/contest/5#problem/0" class="x"><b>Fifth</b> contest</a></td></tr></table>
<a href="https://vjudge.net/contest/1">dup</a>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    seen: list[tuple[str, dict]] = []

    def log_message(self, *args):  # silence
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        Handler.seen.append((url.path, {"query": query, "cookie": self.headers.get("Cookie"), "ua": self.headers.get("User-Agent")}))
        if url.path == "/contest/rank/single/1":
            return self._send(200, ENDED)
        if url.path == "/contest/rank/single/4":
            return self._send(200, RUNNING)
        if url.path == "/contest/rank/single/2":
            if self.headers.get("Cookie") == "Jax.Q=me|secret":
                return self._send(200, ENDED | {"id": 2, "title": "Private contest"})
            return self._send(200, LOGIN_HTML, "text/html; charset=utf-8")
        if url.path == "/contest/rank/single/3":
            return self._send(404, {"error": "not found"})
        if url.path == "/group/demo":
            return self._send(200, GROUP_HTML, "text/html")
        if url.path == "/contest/data":
            rows = [[100, "Public A", 1, 2, "someone"], [101, "Public B", 1, 2, "someone"]]
            if query.get("group") == "demo":
                rows = [[9, "Group contest nine", 1, 2, "admin"]]
            return self._send(200, {"draw": 1, "recordsTotal": len(rows), "recordsFiltered": len(rows), "data": rows})
        return self._send(404, {"error": "no route"})


class ServerMixin:
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Handler.seen.clear()


class ClientTests(ServerMixin, unittest.TestCase):
    def client(self, cookie=None):
        return VJudgeClient(self.base, cookie, delay=0, retries=1)

    def test_contest_rank_and_headers(self):
        data = self.client().contest_rank(1)
        self.assertEqual(data["title"], "Ended contest")
        path, meta = Handler.seen[-1]
        self.assertEqual(path, "/contest/rank/single/1")
        self.assertIn("Mozilla", meta["ua"])
        self.assertIsNone(meta["cookie"])

    def test_private_contest_needs_cookie(self):
        with self.assertRaises(VJudgeAuthError) as ctx:
            self.client().contest_rank(2)
        self.assertIn("login", str(ctx.exception).lower())
        data = self.client("Jax.Q=me|secret").contest_rank(2)
        self.assertEqual(data["title"], "Private contest")

    def test_missing_contest(self):
        with self.assertRaises(VJudgeError):
            self.client().contest_rank(3)

    def test_discover(self):
        found = self.client().discover_group_contests("demo")
        self.assertEqual([c.id for c in found], [1, 5, 9])
        by_id = {c.id: c for c in found}
        self.assertEqual(by_id[1].title, "Ended contest")
        self.assertEqual(by_id[5].title, "Fifth contest")
        self.assertEqual(by_id[9].title, "Group contest nine")
        self.assertTrue(by_id[9].source.startswith("contest feed"))
        # The unfiltered feed (public contests 100/101) must not leak in.
        self.assertNotIn(100, by_id)

    def test_load_cookie_formats(self):
        self.assertEqual(load_cookie("NOPE_UNSET_VAR", None), None)
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "c"
            f.write_text("Cookie: JSESSIONlD=a; Jax.Q=b\n")
            self.assertEqual(load_cookie("NOPE_UNSET_VAR", f), "JSESSIONlD=a; Jax.Q=b")
            f.write_text(json.dumps([{"name": "JSESSIONlD", "value": "a"}, {"name": "Jax.Q", "value": "u|t"}]))
            self.assertEqual(load_cookie("NOPE_UNSET_VAR", f), "JSESSIONlD=a; Jax.Q=u|t")


class FetchCommandTests(ServerMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "jollyrating.toml").write_text(
            f'[vjudge]\nbase_url = "{self.base}"\nrequest_delay = 0\ncookie_file = ".cookie"\n'
            '[[contests]]\nid = 1\n[[contests]]\nid = 4\n[[contests]]\nid = 2\n[[contests]]\nid = 3\n'
        )

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["-c", str(self.root / "jollyrating.toml"), *args])
        return code, out.getvalue()

    def test_fetch_caches_and_refreshes_running_contests(self):
        code, out = self.run_cli("fetch")
        self.assertEqual(code, 0)
        self.assertIn("fetched 2, cached 0, failed 2", out)  # 2 needs a cookie, 3 does not exist
        cache = self.root / "data" / "contests"
        self.assertTrue((cache / "1.json").exists())
        self.assertTrue((cache / "4.json").exists())
        self.assertFalse((cache / "2.json").exists())

        Handler.seen.clear()
        (self.root / ".cookie").write_text("Jax.Q=me|secret")
        code, out = self.run_cli("fetch")
        self.assertEqual(code, 0)
        self.assertIn("fetched 2, cached 1, failed 1", out)  # 1 cached, 4 still running, 2 now ok
        fetched_paths = [p for p, _ in Handler.seen]
        self.assertNotIn("/contest/rank/single/1", fetched_paths)
        self.assertIn("/contest/rank/single/4", fetched_paths)
        self.assertTrue((cache / "2.json").exists())

        code, out = self.run_cli("fetch", "--force", "--ids", "1")
        self.assertIn("fetched 1, cached 0, failed 0", out)

        code, out = self.run_cli("compute", "--ignore-missing")
        self.assertEqual(code, 0)
        self.assertIn("Alice", out)

    def test_discover_write(self):
        (self.root / "jollyrating.toml").write_text(
            f'[vjudge]\nbase_url = "{self.base}"\nrequest_delay = 0\ngroup = "demo"\n[[contests]]\nid = 1\n'
        )
        code, out = self.run_cli("discover", "--write")
        self.assertEqual(code, 0, out)
        self.assertIn("appended 2 contest(s)", out)
        text = (self.root / "jollyrating.toml").read_text()
        self.assertIn("id = 5\n", text)
        self.assertIn("id = 9\n", text)
        code, out = self.run_cli("validate")
        self.assertEqual(code, 0)
        self.assertIn("  - 9:", out)


if __name__ == "__main__":
    unittest.main()
