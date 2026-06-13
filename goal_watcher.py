"""Vigilante de goles del Mundial 2026.

Corre en GitHub Actions cada ~5 min. Consulta API-Football, detecta cuando sube
el marcador de algún partido (gol) y manda un push FCM al tema "goals" — al que
están suscritos todos los teléfonos con la app. El estado se guarda en
live/_seen_scores.json (se commitea de vuelta al repo).

Secrets requeridos (env): APIFOOTBALL_KEY, FIREBASE_SERVICE_ACCOUNT (JSON).
"""
import json
import os
import datetime
from google.oauth2 import service_account
import google.auth.transport.requests
import requests

PROJECT = "world-cup-2026-bb8ee"
SEEN_PATH = "live/_seen_scores.json"
API = "https://v3.football.api-sports.io"
LIVE_STATUSES = {"1H", "2H", "HT", "ET", "BT", "P", "LIVE"}
DONE_STATUSES = {"FT", "AET", "PEN"}


def api_fixtures(api_key):
    today = datetime.datetime.utcnow().date()
    params = {
        "league": "1",
        "season": "2026",
        "from": (today - datetime.timedelta(days=1)).isoformat(),
        "to": (today + datetime.timedelta(days=1)).isoformat(),
    }
    r = requests.get(f"{API}/fixtures", params=params,
                     headers={"x-apisports-key": api_key}, timeout=30)
    r.raise_for_status()
    return r.json().get("response", [])


def load_seen():
    try:
        with open(SEEN_PATH) as f:
            return json.load(f)
    except Exception:
        return None


def fcm_token(sa_json):
    creds = service_account.Credentials.from_service_account_info(
        json.loads(sa_json),
        scopes=["https://www.googleapis.com/auth/firebase.messaging"])
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def send_push(token, title, body):
    msg = {"message": {
        "topic": "goals",
        "notification": {"title": title, "body": body},
        "android": {"priority": "high"},
    }}
    r = requests.post(
        f"https://fcm.googleapis.com/v1/projects/{PROJECT}/messages:send",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        data=json.dumps(msg), timeout=30)
    print("FCM", r.status_code, "-", title, body)


def main():
    api_key = os.environ["APIFOOTBALL_KEY"]
    sa_json = os.environ["FIREBASE_SERVICE_ACCOUNT"]

    fixtures = api_fixtures(api_key)
    cur, info = {}, {}
    for f in fixtures:
        fid = str(f["fixture"]["id"])
        gh = f["goals"]["home"] or 0
        ga = f["goals"]["away"] or 0
        st = f["fixture"]["status"]["short"] or ""
        cur[fid] = gh + ga
        info[fid] = (f["teams"]["home"]["name"], gh, ga,
                     f["teams"]["away"]["name"], st)

    seen = load_seen()
    if seen is None:
        # Primera corrida: sembrar el estado actual SIN enviar nada (evita spam
        # de goles ya ocurridos).
        os.makedirs("live", exist_ok=True)
        with open(SEEN_PATH, "w") as f:
            json.dump(cur, f)
        print("seeded", len(cur), "fixtures")
        return

    token = None
    for fid, total in cur.items():
        if total > seen.get(fid, 0):
            home, gh, ga, away, st = info[fid]
            if token is None:
                token = fcm_token(sa_json)
            extra = " (Final)" if st in DONE_STATUSES else ""
            send_push(token, "⚽ ¡Gol en el Mundial!",
                      f"{home} {gh} - {ga} {away}{extra}")

    seen.update(cur)
    with open(SEEN_PATH, "w") as f:
        json.dump(seen, f)
    print("done")


if __name__ == "__main__":
    main()
