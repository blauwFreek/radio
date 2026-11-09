"""Webinterface: met een knop de JOE-nummers van een dag in een Spotify-afspeellijst zetten."""

from __future__ import annotations

import hmac
import os
from datetime import date, datetime, timedelta
from functools import wraps

from flask import Flask, redirect, render_template, request, session, url_for

import spotify
from joe_source import TZ, fetch_plays

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]
app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(days=365),
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "1") == "1",
    SESSION_COOKIE_SAMESITE="Lax",
)
APP_PASSWORD = os.environ["APP_PASSWORD"]

MAANDEN = ["januari", "februari", "maart", "april", "mei", "juni", "juli",
           "augustus", "september", "oktober", "november", "december"]


def nl_datum(d: date) -> str:
    return f"{d.day} {MAANDEN[d.month - 1]} {d.year}"


def login_required(view):
    @wraps(view)
    def wrapper(*a, **kw):
        if not session.get("ok"):
            return redirect(url_for("login", next=request.path))
        return view(*a, **kw)
    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if hmac.compare_digest(request.form.get("password", ""), APP_PASSWORD):
            session.permanent = True
            session["ok"] = True
            nxt = request.args.get("next", "/")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")
        error = "Onjuist wachtwoord"
    return render_template("login.html", error=error)


@app.route("/")
@login_required
def index():
    today = datetime.now(TZ).date()
    return render_template(
        "index.html",
        connected=spotify.is_connected(),
        today=today.isoformat(),
        default_name=f"JOE – {nl_datum(today)}",
    )


@app.route("/run", methods=["POST"])
@login_required
def run():
    try:
        day = date.fromisoformat(request.form.get("date", ""))
    except ValueError:
        day = datetime.now(TZ).date()
    name = (request.form.get("name") or "").strip() or f"JOE – {nl_datum(day)}"
    public = request.form.get("public") == "1"
    dedupe = request.form.get("dedupe") == "1"
    preview = request.form.get("action") == "preview"

    result = fetch_plays(day)
    rows, track_ids, error, playlist = [], [], None, None
    try:
        seen = set()
        for p in result.plays:
            tid = p.spotify_id
            status = "link"
            if not tid and p.title and spotify.is_connected():
                tid = spotify.search_track(p.artist, p.title)
                status = "gezocht" if tid else "niet gevonden"
            elif not tid:
                status = "geen link"
            if tid and dedupe and tid in seen:
                status = "dubbel, overgeslagen"
            elif tid:
                seen.add(tid)
                track_ids.append(tid)
            rows.append({"label": p.label(), "status": status, "id": tid})

        if not preview:
            if not track_ids:
                error = "Geen nummers gevonden voor deze dag; er is geen afspeellijst gemaakt."
            else:
                playlist = spotify.create_playlist(
                    name, f"Nummers die JOE draaide op {nl_datum(day)}.", public)
                spotify.add_tracks(playlist["id"], track_ids)
    except spotify.SpotifyError as e:
        error = str(e)

    return render_template(
        "result.html", day=nl_datum(day), name=name, rows=rows, count=len(track_ids),
        playlist=playlist, preview=preview, error=error, log=result.log,
    )


@app.route("/spotify/connect")
@login_required
def spotify_connect():
    session["oauth_state"] = spotify.new_state()
    return redirect(spotify.authorize_url(session["oauth_state"]))


@app.route("/callback")
@login_required
def spotify_callback():
    if request.args.get("state") != session.pop("oauth_state", None):
        return "Ongeldige state, probeer opnieuw te koppelen.", 400
    if "code" not in request.args:
        return f"Koppelen geannuleerd: {request.args.get('error', 'onbekend')}", 400
    spotify.exchange_code(request.args["code"])
    return redirect(url_for("index"))


@app.route("/spotify/disconnect", methods=["POST"])
@login_required
def spotify_disconnect():
    spotify.disconnect()
    return redirect(url_for("index"))


@app.route("/health")
def health():
    return "ok"
