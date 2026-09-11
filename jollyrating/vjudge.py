"""A small HTTP client for the endpoints vjudge's own frontend uses.

vjudge has no public API.  Two endpoints are used here:

* ``GET /contest/rank/single/<id>`` – contest standings as JSON
  (``participants``, ``submissions``, ``begin``, ``length``, ``title``).
* ``GET /group/<name>`` and ``GET /contest/data`` – best-effort discovery of
  the contests of a group.

Private (group) contests need the cookies of a logged-in vjudge session.
Copy the ``Cookie`` request header from your browser's devtools (the login is
``JSESSIONlD=<user id>|<token>``, spelled with a lowercase L) into the
``VJUDGE_COOKIE`` environment variable or the file named by ``cookie_file``.
vjudge sits behind Cloudflare; when the header carries a ``cf_clearance``
cookie, requests are only accepted from the same IP address and with the same
``User-Agent`` the browser used, so set ``user_agent`` in the config to your
browser's value when fetching from your own machine.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://vjudge.net"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 JollyRating/0.1 (+https://github.com/VennethN/JollyRating)"
)


class VJudgeError(RuntimeError):
    pass


class VJudgeAuthError(VJudgeError):
    """vjudge answered with its login page / an authorisation error."""


@dataclass(frozen=True)
class DiscoveredContest:
    id: int
    title: str
    source: str


def load_cookie(env_var: str = "VJUDGE_COOKIE", cookie_file: Path | None = None) -> str | None:
    """Return the raw ``Cookie`` header value to send, if any."""
    value = os.environ.get(env_var, "").strip()
    if not value and cookie_file is not None and cookie_file.exists():
        value = cookie_file.read_text(encoding="utf-8").strip()
    if not value:
        return None
    if value.lower().startswith("cookie:"):
        value = value.split(":", 1)[1].strip()
    # Accept a cookie-exporter JSON array [{"name": ..., "value": ...}, ...] too.
    if value.startswith("["):
        try:
            pairs = [(c["name"], c["value"]) for c in json.loads(value) if "name" in c and "value" in c]
            value = "; ".join(f"{k}={v}" for k, v in pairs)
        except (ValueError, TypeError, KeyError):
            pass
    return value.replace("\n", " ").strip() or None


class VJudgeClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        cookie: str | None = None,
        *,
        delay: float = 1.0,
        timeout: float = 30.0,
        retries: int = 3,
        session: requests.Session | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent or USER_AGENT,
                "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "X-Requested-With": "XMLHttpRequest",
            }
        )
        if cookie:
            self.session.headers["Cookie"] = cookie
        self._last_request = 0.0

    @property
    def authenticated(self) -> bool:
        return "Cookie" in self.session.headers

    # ------------------------------------------------------------------ #

    def _throttle(self) -> None:
        wait = self.delay - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _get(self, path: str, params: dict[str, Any] | None = None, *, referer: str | None = None) -> requests.Response:
        url = f"{self.base_url}{path}"
        headers = {"Referer": referer or f"{self.base_url}/"}
        last_exc: Exception | None = None
        for attempt in range(1, self.retries + 1):
            self._throttle()
            try:
                resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                last_exc = exc
                log.warning("GET %s failed (%s), attempt %d/%d", url, exc, attempt, self.retries)
                time.sleep(min(2 ** attempt, 15))
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                log.warning("GET %s -> HTTP %d, retrying (%d/%d)", url, resp.status_code, attempt, self.retries)
                time.sleep(min(2 ** attempt, 15))
                continue
            return resp
        raise VJudgeError(f"GET {url} failed after {self.retries} attempts: {last_exc}")

    @staticmethod
    def _looks_like_html(resp: requests.Response) -> bool:
        ctype = resp.headers.get("Content-Type", "")
        body = resp.text.lstrip()[:200].lower()
        return "text/html" in ctype or body.startswith("<!doctype") or body.startswith("<html")

    def get_json(self, path: str, params: dict[str, Any] | None = None, **kw: Any) -> Any:
        resp = self._get(path, params, **kw)
        if resp.status_code in (401, 403):
            raise VJudgeAuthError(f"{resp.url}: HTTP {resp.status_code} – log in (set the cookie) or check group membership")
        if resp.status_code == 404:
            raise VJudgeError(f"{resp.url}: HTTP 404 – no such contest/group")
        if resp.status_code != 200:
            raise VJudgeError(f"{resp.url}: HTTP {resp.status_code}: {resp.text[:200]!r}")
        if self._looks_like_html(resp):
            hint = "vjudge returned an HTML page instead of JSON"
            if "login" in resp.text.lower():
                hint += " (a login page: the contest is private, set the cookie)"
            raise VJudgeAuthError(f"{resp.url}: {hint}")
        try:
            return resp.json()
        except ValueError as exc:
            raise VJudgeError(f"{resp.url}: invalid JSON: {resp.text[:200]!r}") from exc

    # ------------------------------------------------------------------ #

    def contest_rank(self, contest_id: int | str) -> dict[str, Any]:
        """Raw payload of ``/contest/rank/single/<id>``."""
        data = self.get_json(f"/contest/rank/single/{int(contest_id)}", referer=f"{self.base_url}/contest/{int(contest_id)}")
        if not isinstance(data, dict):
            raise VJudgeError(f"contest {contest_id}: unexpected payload type {type(data).__name__}")
        if "error" in data and not data.get("participants"):
            raise VJudgeError(f"contest {contest_id}: {data.get('error')}")
        if "participants" not in data or "submissions" not in data:
            keys = ", ".join(sorted(map(str, data)))
            raise VJudgeError(f"contest {contest_id}: payload has no participants/submissions (keys: {keys})")
        return data

    def contest_list(self, **params: Any) -> dict[str, Any]:
        """``/contest/data`` – the DataTables feed behind https://vjudge.net/contest."""
        query: dict[str, Any] = {
            "draw": 1, "start": 0, "length": 100, "sortDir": "desc", "sortCol": 0,
            "category": "all", "running": 0, "title": "", "owner": "",
        }
        query.update(params)
        data = self.get_json("/contest/data", query, referer=f"{self.base_url}/contest")
        if not isinstance(data, dict) or "data" not in data:
            raise VJudgeError("/contest/data: unexpected payload")
        return data

    # ------------------------------------------------------------------ #
    # Group discovery
    # ------------------------------------------------------------------ #

    _LINK_RE = re.compile(r'href="(?:https?://[^/"]+)?/contest/(\d+)(?:[/?#][^"]*)?"[^>]*>(.*?)</a>', re.I | re.S)
    _ID_RE = re.compile(r"/contest/(\d+)")
    _GROUP_ID_RE = re.compile(r'(?:groupId|group_id|data-group-id)\W{0,4}(\d+)', re.I)

    def group_page(self, group: str) -> str:
        resp = self._get(f"/group/{group}")
        if resp.status_code == 404:
            raise VJudgeError(f"group {group!r} not found (the name is the last part of https://vjudge.net/group/<name>)")
        if resp.status_code in (401, 403):
            raise VJudgeAuthError(f"group {group!r}: HTTP {resp.status_code} – private group, set the cookie")
        if resp.status_code != 200:
            raise VJudgeError(f"group {group!r}: HTTP {resp.status_code}")
        return resp.text

    @staticmethod
    def _rows_to_contests(rows: Any, source: str) -> list[DiscoveredContest]:
        found: list[DiscoveredContest] = []
        for row in rows or []:
            cid: int | None = None
            title = ""
            if isinstance(row, dict):
                for key in ("id", "contestId", "contest_id"):
                    if isinstance(row.get(key), int):
                        cid = row[key]
                        break
                title = str(row.get("title") or row.get("name") or "")
            elif isinstance(row, (list, tuple)):
                ints = [v for v in row if isinstance(v, int) and not isinstance(v, bool)]
                strs = [v for v in row if isinstance(v, str) and v.strip()]
                cid = ints[0] if ints else None
                title = strs[0] if strs else ""
            if cid is not None:
                found.append(DiscoveredContest(cid, re.sub(r"<[^>]+>", "", title).strip(), source))
        return found

    def discover_group_contests(self, group: str) -> list[DiscoveredContest]:
        """Best-effort list of the contests shown on a group page.

        Strategy 1 scrapes ``/contest/<id>`` links from the group page HTML.
        Strategy 2 asks the contest DataTables feed with a ``group`` filter and
        keeps the answer only if it differs from the unfiltered feed (so an
        ignored parameter cannot pull in unrelated public contests).
        """
        found: dict[int, DiscoveredContest] = {}

        html = self.group_page(group)
        for cid, text in self._LINK_RE.findall(html):
            title = re.sub(r"<[^>]+>", "", text).strip()
            found.setdefault(int(cid), DiscoveredContest(int(cid), title, "group page"))
        for cid in self._ID_RE.findall(html):
            found.setdefault(int(cid), DiscoveredContest(int(cid), "", "group page"))
        log.info("group page %s: %d contest links", group, len(found))

        group_ids = [group] + list(dict.fromkeys(self._GROUP_ID_RE.findall(html)))
        try:
            baseline = json.dumps(self.contest_list().get("data"), sort_keys=True)
        except VJudgeError as exc:
            log.info("contest feed unavailable (%s); skipping strategy 2", exc)
            baseline = None
        if baseline is not None:
            for key in ("group", "groupId"):
                for value in group_ids:
                    try:
                        data = self.contest_list(**{key: value})
                    except VJudgeError as exc:
                        log.info("contest feed with %s=%s failed: %s", key, value, exc)
                        continue
                    if json.dumps(data.get("data"), sort_keys=True) == baseline:
                        log.info("contest feed ignores %s=%s (same answer as unfiltered)", key, value)
                        continue
                    rows = self._rows_to_contests(data.get("data"), f"contest feed ({key}={value})")
                    log.info("contest feed with %s=%s: %d contests", key, value, len(rows))
                    for c in rows:
                        found.setdefault(c.id, c)
                    if rows:
                        break
                else:
                    continue
                break
        return sorted(found.values(), key=lambda c: c.id)
