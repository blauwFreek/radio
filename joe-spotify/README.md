# JOE → Spotify

Een kleine webpagina voor je eigen Hetzner-server. Je opent de pagina (bijvoorbeeld op de iPad), vult een naam in, kiest een dag en drukt op **Maak afspeellijst**. De nummers die JOE die dag heeft gedraaid komen dan in een nieuwe Spotify-afspeellijst.

## Wat je nodig hebt
- Een Hetzner-server met Docker en Docker Compose.
- Een (sub)domein met een A-record dat naar die server wijst, bijvoorbeeld `joe.jouwdomein.nl`. Spotify eist https, en Caddy regelt dat automatisch.
- Een Spotify-account. Voor apps in "development mode" vraagt Spotify een Premium-account.

## 1. Spotify-app aanmaken (eenmalig)
1. Ga naar https://developer.spotify.com/dashboard en klik op **Create app**.
2. Vul bij Redirect URI in: `https://joe.jouwdomein.nl/callback`. Gebruik je eigen domein.
3. Vink **Web API** aan en sla op.
4. Kopieer de **Client ID** en de **Client Secret**.

## 2. Op de server zetten
```bash
git clone <deze repo> && cd <repo>/joe-spotify
cp .env.example .env
nano .env                   # vul DOMAIN, SPOTIFY_*, APP_PASSWORD en SECRET_KEY in
docker compose up -d --build
```

## 3. Gebruiken op de iPad
1. Open `https://joe.jouwdomein.nl` in Safari en log in met je `APP_PASSWORD`.
2. Tik eenmalig op **Spotify koppelen** en geef toestemming.
3. Optioneel: tik op Delen → **Zet op beginscherm**. Dan heb je er een app-icoon voor.
4. Vul een naam in, kies de dag en tik op **Maak afspeellijst**. Daarna kun je met **Open in Spotify** direct naar de lijst.

Met **Eerst bekijken welke nummers** zie je de lijst van tevoren, zonder dat er een afspeellijst wordt gemaakt.

## Waar komen de nummers vandaan?
Uit de JSON-API van JOE:
`https://api.joe.nl/2.0/tracks/plays?_station_id=joe_nl&limit=500`

Elke aanroep geeft maximaal 500 nummers terug, ongeveer anderhalve dag. Voor de gekozen dag vraagt de app de lijst op tot middernacht, met `upto=`. Waar nodig bladert hij via `pagination.previous` verder terug, zodat ook eerdere dagen werken. Een paar nummers hebben bij JOE geen Spotify-link. Die worden op artiest en titel opgezocht in Spotify.

Controleren wat er gevonden wordt kan direct op de server:
```bash
docker compose exec app python joe_source.py            # vandaag
docker compose exec app python joe_source.py 2026-11-08 # andere dag
```
Op de resultaatpagina zie je hetzelfde onder **Technische details**. Verandert JOE ooit de API, dan kun je een andere URL instellen met `JOE_API_URLS` in `.env`.
