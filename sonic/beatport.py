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
import json
import os
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


def _scrape_docs_client_id() -> str:
    page = _http(DOCS_CLIENT_ID_URL).decode("utf-8", "ignore")
    for marker in ('"client_id":"', "client_id: '", 'client_id="'):
        i = page.find(marker)
        if i >= 0:
            j = i + len(marker)
            end = min(x for x in (page.find('"', j), page.find("'", j)) if x > 0)
            return page[j:end]
    raise RuntimeError("could not locate docs client_id — page layout changed; "
                       "pass BEATPORT_CLIENT_ID explicitly")


def get_token() -> str:
    tok = os.environ.get("BEATPORT_TOKEN")
    if tok:
        return tok
    user = os.environ.get("BEATPORT_USERNAME")
    pw = os.environ.get("BEATPORT_PASSWORD")
    if not (user and pw):
        raise RuntimeError("set BEATPORT_TOKEN, or BEATPORT_USERNAME + BEATPORT_PASSWORD")
    client_id = os.environ.get("BEATPORT_CLIENT_ID") or _scrape_docs_client_id()
    body = urllib.parse.urlencode({
        "grant_type": "password", "username": user, "password": pw,
        "client_id": client_id}).encode()
    resp = json.loads(_http(f"{API}/auth/o/token/", data=body,
                            headers={"Content-Type": "application/x-www-form-urlencoded"}))
    if "access_token" not in resp:
        raise RuntimeError(f"auth failed: {resp}")
    return resp["access_token"]


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
