"""Minimale Spotify Web API-client (Authorization Code flow met refresh token)."""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
import urllib.parse
from pathlib import Path

import requests

CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI", "")
TOKEN_FILE = Path(os.environ.get("TOKEN_FILE", "data/spotify_token.json"))
SCOPES = "playlist-modify-private playlist-modify-public"

API = "https://api.spotify.com/v1"


class SpotifyError(RuntimeError):
    pass


# ------------------------------------------------------------------ auth ---

def authorize_url(state: str) -> str:
    q = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
    })
    return f"https://accounts.spotify.com/authorize?{q}"


def _token_request(data: dict) -> dict:
    basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    r = requests.post(
        "https://accounts.spotify.com/api/token",
        data=data,
        headers={"Authorization": f"Basic {basic}"},
        timeout=20,
    )
    if not r.ok:
        raise SpotifyError(f"Token-aanvraag mislukt: {r.status_code} {r.text}")
    tok = r.json()
    tok["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
    return tok


def _save(tok: dict) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(tok))
    TOKEN_FILE.chmod(0o600)


def exchange_code(code: str) -> None:
    _save(_token_request({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }))


def is_connected() -> bool:
    return TOKEN_FILE.exists()


def disconnect() -> None:
    TOKEN_FILE.unlink(missing_ok=True)


def _access_token() -> str:
    if not TOKEN_FILE.exists():
        raise SpotifyError("Spotify is nog niet gekoppeld.")
    tok = json.loads(TOKEN_FILE.read_text())
    if time.time() >= tok.get("expires_at", 0):
        new = _token_request({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        new.setdefault("refresh_token", tok["refresh_token"])
        _save(new)
        tok = new
    return tok["access_token"]


def new_state() -> str:
    return secrets.token_urlsafe(16)


# ------------------------------------------------------------------- api ---

def _call(method: str, path: str, **kw) -> requests.Response:
    for _ in range(3):
        r = requests.request(
            method, f"{API}{path}",
            headers={"Authorization": f"Bearer {_access_token()}"},
            timeout=20, **kw,
        )
        if r.status_code == 429:  # rate limit
            time.sleep(min(int(r.headers.get("Retry-After", "2")), 10))
            continue
        return r
    return r


def _ok(r: requests.Response, what: str) -> dict:
    if not r.ok:
        raise SpotifyError(f"{what} mislukt: {r.status_code} {r.text[:300]}")
    return r.json() if r.content else {}


def search_track(artist: str, title: str) -> str | None:
    q = f'track:"{title}"' + (f' artist:"{artist}"' if artist else "")
    r = _call("GET", "/search", params={"q": q, "type": "track", "limit": 1, "market": "NL"})
    items = _ok(r, "Zoeken").get("tracks", {}).get("items", [])
    if not items and artist:  # losser zoeken
        r = _call("GET", "/search", params={"q": f"{artist} {title}", "type": "track", "limit": 1, "market": "NL"})
        items = _ok(r, "Zoeken").get("tracks", {}).get("items", [])
    return items[0]["id"] if items else None


def create_playlist(name: str, description: str, public: bool) -> dict:
    body = {"name": name, "description": description, "public": public}
    r = _call("POST", "/me/playlists", json=body)
    if r.status_code in (404, 405):  # oudere API-variant
        me = _ok(_call("GET", "/me"), "Profiel ophalen")
        r = _call("POST", f"/users/{me['id']}/playlists", json=body)
    return _ok(r, "Afspeellijst maken")


def add_tracks(playlist_id: str, track_ids: list[str]) -> None:
    uris = [f"spotify:track:{t}" for t in track_ids]
    for i in range(0, len(uris), 100):
        chunk = {"uris": uris[i:i + 100]}
        r = _call("POST", f"/playlists/{playlist_id}/items", json=chunk)
        if r.status_code in (404, 405):  # oudere API-variant
            r = _call("POST", f"/playlists/{playlist_id}/tracks", json=chunk)
        _ok(r, "Nummers toevoegen")
