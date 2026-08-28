"""Beatport v4 wiring. Written against the documented v4 surface; the live
fetch is the one seam this repo cannot test offline — verify with
`python -m sonic.beatport genres` before trusting anything.

Auth, in order of preference:
  1. BEATPORT_TOKEN env var — a bearer token you already hold.
  2. BEATPORT_USERNAME / BEATPORT_PASSWORD — the community route: the
     public client_id Beatport ships for its own docs frontend, password
     grant against /auth/o/token/. This is the beets-beatport4 approach.
     It is grey: read-only and low-volume, but the ToS call is yours.

Preview handling: download to a temp file, analyse, delete. Vectors are
kept; audio is not.
"""
from __future__ import annotations
import argparse
import http.cookiejar
import json
import os
import re
import tempfile
import urllib.parse
import urllib.request
from .ingest import TrackSighting

API = "https://api.beatport.com/v4"
DOCS_CLIENT_ID_URL = "https://api.beatport.com/v4/docs/"  # client_id lives in this page's js config

UA = {"User-Agent": "signal-sound-layer/0.1 (research; contact via earlysignal.live)"}


def _http(url: str, data: bytes | None = None, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


_CLIENT_ID_RE = re.compile(r"API_CLIENT_ID: \'(.*?)\'")
_REDIRECT_URI = f"{API}/auth/o/post-message/"


def _scrape_docs_client_id() -> str:
    page = _http(DOCS_CLIENT_ID_URL).decode("utf-8", "ignore")
    m = _CLIENT_ID_RE.search(page)
    if m:
        return m.group(1)
    raise RuntimeError("could not locate docs client_id — page layout changed; "
                       "pass BEATPORT_CLIENT_ID explicitly")


def get_token() -> str:
    """Session-login + authorization_code flow, as the beets-beatport4
    project does it: JSON login for cookies, authorize for a code in the
    redirect Location, token exchange with query-string params."""
    tok = os.environ.get("BEATPORT_TOKEN")
    if tok:
        return tok
    user = os.environ.get("BEATPORT_USERNAME")
    pw = os.environ.get("BEATPORT_PASSWORD")
    if not (user and pw):
        raise RuntimeError("set BEATPORT_TOKEN, or BEATPORT_USERNAME + BEATPORT_PASSWORD")
    client_id = os.environ.get("BEATPORT_CLIENT_ID") or _scrape_docs_client_id()

    jar = http.cookiejar.CookieJar()

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar), _NoRedirect)
    opener.addheaders = list(UA.items())

    # 1. login for session cookies
    req = urllib.request.Request(
        f"{API}/auth/login/",
        data=json.dumps({"username": user, "password": pw}).encode(),
        headers={"Content-Type": "application/json"})
    with opener.open(req, timeout=30) as r:
        login = json.loads(r.read())
    if "username" not in login:
        raise RuntimeError(f"beatport login failed: {login}")

    # 2. authorize — the code arrives in the redirect Location header
    q = urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id,
        "redirect_uri": _REDIRECT_URI})
    try:
        resp = opener.open(f"{API}/auth/o/authorize/?{q}", timeout=30)
        location = resp.headers.get("Location", "")
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308):
            raise RuntimeError(f"authorize failed: HTTP {e.code}: {e.read()[:200]}")
        location = e.headers.get("Location", "")
    codes = urllib.parse.parse_qs(urllib.parse.urlparse(location).query).get("code")
    if not codes:
        raise RuntimeError(f"no authorization code in redirect: {location!r}")

    # 3. exchange (params in the query string, per the working implementation)
    q = urllib.parse.urlencode({
        "code": codes[0], "grant_type": "authorization_code",
        "redirect_uri": _REDIRECT_URI, "client_id": client_id})
    req = urllib.request.Request(f"{API}/auth/o/token/?{q}", data=b"")
    with opener.open(req, timeout=30) as r:
        data = json.loads(r.read())
    if "access_token" not in data:
        raise RuntimeError(f"token exchange failed: {data}")
    return data["access_token"]


def _get(path: str, token: str, params: dict | None = None) -> dict:
    q = ("?" + urllib.parse.urlencode(params)) if params else ""
    return json.loads(_http(f"{API}{path}{q}", headers={"Authorization": f"Bearer {token}"}))


def fetch_genres(token: str) -> list[dict]:
    out, page = [], f"/catalog/genres/?per_page=100"
    while page:
        d = _get(page.replace(API, ""), token) if page.startswith(API) else _get(page, token)
        out += d.get("results", [])
        page = d.get("next")
    return out


def fetch_genre_top100(token: str, genre_id: int) -> list[dict]:
    d = _get(f"/catalog/genres/{genre_id}/top/100/", token)
    return d.get("results", d if isinstance(d, list) else [])


class BeatportChartSource:
    """Weekly sightings from genre Top 100s, mapped through scene_map.json:
       { "<beatport_genre_id>": {"scene": "<signal scene>", "name": "..."} }"""

    def __init__(self, scene_map_path: str, token: str | None = None):
        with open(scene_map_path) as f:
            self.scene_map = json.load(f)
        self.token = token or get_token()

    def sightings(self, week: str) -> list[TrackSighting]:
        out = []
        for gid, cfg in self.scene_map.items():
            if gid.startswith("_"):
                continue
            for rank, tr in enumerate(fetch_genre_top100(self.token, int(gid)), 1):
                preview = ((tr.get("sample_url") or
                            (tr.get("preview") or {}).get("mp3", {}).get("url")) or "")
                out.append(TrackSighting(
                    track_id=f"bp:{tr['id']}",
                    audio_ref=preview,
                    scene=cfg["scene"], week=week,
                    source=f"beatport:genre{gid}:top100",
                    chart_rank=rank))
        return out


def download_preview(url: str) -> str:
    """Fetch a preview to a temp file; caller analyses then deletes."""
    fd, path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    with open(path, "wb") as f:
        f.write(_http(url))
    return path


def analyse_sighting(analyser, s: TrackSighting):
    """Download-analyse-discard wrapper for URL-based audio refs."""
    path = download_preview(s.audio_ref)
    try:
        return analyser.analyse(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser(description="beatport wiring checks")
    ap.add_argument("cmd", choices=["genres", "chart"])
    ap.add_argument("--genre-id", type=int)
    args = ap.parse_args()
    token = get_token()
    if args.cmd == "genres":
        for g in fetch_genres(token):
            print(g.get("id"), "\t", g.get("name"), "\t", g.get("slug"))
    else:
        for i, t in enumerate(fetch_genre_top100(token, args.genre_id), 1):
            print(i, t.get("id"), (t.get("name") or "")[:40],
                  "preview:", bool(t.get("sample_url") or t.get("preview")))


if __name__ == "__main__":
    main()
