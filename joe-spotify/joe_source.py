"""Haalt de nummers op die JOE op een bepaalde dag heeft gedraaid.

De precieze bron van joe.nl/playlist is niet gedocumenteerd, dus dit module
probeert meerdere strategieen na elkaar:

1. JSON-API('s) uit JOE_API_URLS (komma-gescheiden, mag {date} bevatten).
2. De HTML van JOE_PLAYLIST_URL: ingebedde JSON (__NEXT_DATA__ e.d.) en
   daarna, als laatste redmiddel, losse Spotify-links in volgorde.

Uit elke JSON wordt recursief alles gehaald dat op een "play" lijkt:
een object met een tijdstip + titel/artiest en/of een Spotify-verwijzing.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo("Europe/Amsterdam")
USER_AGENT = "Mozilla/5.0 (joe-spotify-playlist; persoonlijk gebruik)"

PLAYLIST_URL = os.environ.get("JOE_PLAYLIST_URL", "https://joe.nl/playlist")
DEFAULT_API_URLS = "https://api.joe.nl/2.0/tracks/plays?_station_id=joe_nl&limit=500"
MAX_PAGES = 10  # veiligheidsgrens bij terugbladeren
API_URLS = [
    u.strip()
    for u in os.environ.get("JOE_API_URLS", DEFAULT_API_URLS).split(",")
    if u.strip()
]

SPOTIFY_ID_RE = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[a-z]{2}/)?track/|spotify:track:|spotify%3Atrack%3A)"
    r"([A-Za-z0-9]{22})"
)
TIME_KEYS = ("played_at", "playedAt", "played", "start", "start_time", "startTime",
             "timestamp", "time", "datetime", "date")
TITLE_KEYS = ("title", "name", "track_title", "trackTitle", "song")
ARTIST_KEYS = ("artist", "artists", "artist_name", "artistName", "performer")
SPOTIFY_KEYS = ("spotify_id", "spotifyId", "spotify_track_id", "spotifyTrackId")


@dataclass
class Play:
    title: str = ""
    artist: str = ""
    spotify_id: str | None = None
    played_at: datetime | None = None
    source: str = ""

    def label(self) -> str:
        t = self.played_at.strftime("%H:%M") if self.played_at else "--:--"
        return f"{t}  {self.artist} - {self.title}".strip()


@dataclass
class FetchResult:
    plays: list[Play] = field(default_factory=list)
    log: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- helpers ---

def _http_get(url: str) -> requests.Response:
    return requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.9"},
        timeout=20,
    )


def _parse_time(value) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            ts = value / 1000 if value > 10**11 else value
            return datetime.fromtimestamp(ts, TZ)
        if isinstance(value, str) and value:
            s = value.strip().replace("Z", "+00:00")
            if s.isdigit():
                return _parse_time(int(s))
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TZ)
            return dt.astimezone(TZ)
    except (ValueError, OSError, OverflowError):
        return None
    return None


def _text(value) -> str:
    """Maak van een string/dict/lijst (bv. artist-object) een leesbare naam."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for k in ("name", "title", "display_name"):
            if isinstance(value.get(k), str):
                return value[k].strip()
        return ""
    if isinstance(value, list):
        return ", ".join(filter(None, (_text(v) for v in value)))
    return ""


def _spotify_in(obj, depth: int = 0) -> str | None:
    """Zoek een Spotify track-ID binnen dit object (niet te diep)."""
    if depth > 3:
        return None
    if isinstance(obj, str):
        m = SPOTIFY_ID_RE.search(obj)
        return m.group(1) if m else None
    if isinstance(obj, dict):
        for k in SPOTIFY_KEYS:
            v = obj.get(k)
            if isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9]{22}", v):
                return v
        for v in obj.values():
            found = _spotify_in(v, depth + 1)
            if found:
                return found
    if isinstance(obj, list):
        for v in obj:
            found = _spotify_in(v, depth + 1)
            if found:
                return found
    return None


def _as_play(obj: dict, source: str) -> Play | None:
    played_at = next((p for k in TIME_KEYS if (p := _parse_time(obj.get(k)))), None)
    title = next((t for k in TITLE_KEYS if (t := _text(obj.get(k)))), "")
    artist = next((a for k in ARTIST_KEYS if (a := _text(obj.get(k)))), "")
    # Soms zit titel/artiest een niveau dieper, bv. {"played_at":..., "track": {...}}
    if not title:
        for sub in ("track", "song", "item"):
            inner = obj.get(sub)
            if isinstance(inner, dict):
                title = title or next((t for k in TITLE_KEYS if (t := _text(inner.get(k)))), "")
                artist = artist or next((a for k in ARTIST_KEYS if (a := _text(inner.get(k)))), "")
    spotify_id = _spotify_in(obj)
    if played_at and (title or spotify_id):
        return Play(title, artist, spotify_id, played_at, source)
    return None


def _walk(obj, source: str, out: list[Play]) -> None:
    if isinstance(obj, dict):
        play = _as_play(obj, source)
        if play:
            out.append(play)
            return
        for v in obj.values():
            _walk(v, source, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, source, out)


def _embedded_json(html: str) -> list:
    blobs = []
    for m in re.finditer(
        r"<script[^>]*(?:id=\"__NEXT_DATA__\"|type=\"application/(?:ld\+)?json\")[^>]*>(.*?)</script>",
        html, re.S,
    ):
        try:
            blobs.append(json.loads(m.group(1)))
        except ValueError:
            pass
    m = re.search(r"window\.__(?:NUXT|INITIAL_STATE|PRELOADED_STATE)__\s*=\s*(\{.*?\});?\s*</script>", html, re.S)
    if m:
        try:
            blobs.append(json.loads(m.group(1)))
        except ValueError:
            pass
    return blobs


def _fetch_api(template: str, day: date, log: list[str]) -> list[Play]:
    """Haal plays op en blader via pagination.previous terug tot vóór `day`."""
    from urllib.parse import urlencode, urljoin

    day_start = datetime.combine(day, time.min, TZ)
    upto = (day_start + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S %z")
    url = template.replace("{date}", day.isoformat())
    if "{date}" not in template and "upto=" not in url:
        url += ("&" if "?" in url else "?") + urlencode({"upto": upto})

    found: list[Play] = []
    for page in range(MAX_PAGES):
        try:
            r = _http_get(url)
            if r.status_code != 200:
                log.append(f"API {url}: HTTP {r.status_code}")
                break
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            log.append(f"API {url}: fout {e.__class__.__name__}: {e}")
            break
        batch: list[Play] = []
        _walk(data, "api", batch)
        found += batch
        log.append(f"API pagina {page + 1}: {len(batch)} plays")
        oldest = min((p.played_at for p in batch if p.played_at), default=None)
        prev = (data.get("pagination") or {}).get("previous") if isinstance(data, dict) else None
        if not batch or not prev or (oldest and oldest < day_start):
            break
        url = urljoin(url, prev)
    return found


# ------------------------------------------------------------------ main ---

def fetch_plays(day: date) -> FetchResult:
    res = FetchResult()
    found: list[Play] = []

    for template in API_URLS:
        found = _fetch_api(template, day, res.log)
        if found:
            break

    html_ids: list[str] = []
    if not found:
        try:
            r = _http_get(PLAYLIST_URL)
            res.log.append(f"HTML {PLAYLIST_URL}: HTTP {r.status_code}, {len(r.text)} tekens")
            if r.ok:
                for blob in _embedded_json(r.text):
                    _walk(blob, "html-json", found)
                res.log.append(f"HTML ingebedde JSON: {len(found)} plays gevonden")
                if not found:
                    html_ids = list(dict.fromkeys(SPOTIFY_ID_RE.findall(r.text)))
                    res.log.append(f"HTML losse Spotify-links: {len(html_ids)} gevonden "
                                   "(zonder tijd, alleen bruikbaar voor vandaag)")
        except requests.RequestException as e:
            res.log.append(f"HTML {PLAYLIST_URL}: fout {e}")

    day_plays = [p for p in found if p.played_at and p.played_at.date() == day]
    if found:
        res.log.append(f"Waarvan op {day.isoformat()}: {len(day_plays)}")
    elif html_ids and day == datetime.now(TZ).date():
        day_plays = [Play(spotify_id=i, source="html-link") for i in html_ids]

    day_plays.sort(key=lambda p: p.played_at or datetime.min.replace(tzinfo=TZ))
    res.plays = day_plays
    return res


if __name__ == "__main__":  # handig om op de server te testen
    import sys

    d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else datetime.now(TZ).date()
    result = fetch_plays(d)
    print("\n".join(result.log))
    for p in result.plays:
        print(p.label(), p.spotify_id or "(geen Spotify-ID)")
