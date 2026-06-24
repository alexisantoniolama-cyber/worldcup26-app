"""Actualizador del feed EN VIVO del Mundial 2026 (live/live.json).

Corre en GitHub Actions en bucle (ver update-feed.yml). Consulta API-Football y
reescribe live/live.json con:
  - results:    marcador de partidos TERMINADOS y EN VIVO (con minuto y tarjetas
                por equipo), mapeados al id del fixture propio (M001, ...).
  - rankings:   ranking FIFA (fijo; la API no da el ranking mundial).
  - topScorers: goleadores del torneo.
  - bookings:   amonestados del torneo (amarillas/rojas por jugador).

Goleadores, amonestados y tarjetas se arman desde los EVENTOS de cada partido
(/fixtures/events), no desde /players/topscorers — que en API-Football tarda
HORAS en sumar los goles por jugador. Los eventos están al minuto.

Para cuidar la cuota: los eventos de un partido TERMINADO no cambian, así que se
cachean en live/events_cache.json y no se vuelven a pedir; solo se piden los de
partidos en vivo y los de partidos recién terminados.

La API key vive como secret APIFOOTBALL_KEY (o tool/api_key.txt en local), nunca
en el APK. Sin dependencias externas: solo la libreria estandar de Python.
"""
import datetime
import json
import os
import re
import urllib.request
from collections import defaultdict

API = "https://v3.football.api-sports.io"
LEAGUE = "1"
SEASON = "2026"
MATCHES_PATH = "live/matches.json"
OUT_PATH = "live/live.json"
EVENTS_CACHE = "live/events_cache.json"

LIVE_STATUSES = {"1H", "2H", "HT", "ET", "BT", "P", "LIVE", "INT", "SUSP"}
DONE_STATUSES = {"FT", "AET", "PEN"}

# Ronda de la API (substring del campo league.round) -> etapa en mi matches.json.
# Se usa para mapear los partidos de ELIMINATORIA (que en el fixture propio vienen
# con homeCode/awayCode en null) a su cupo de llave, y así llenar quién avanzó.
# La API recién agrega estos partidos cuando se definen las llaves; hasta entonces
# este mapeo no encuentra nada y el cuadro queda con sus etiquetas ("2° Grupo A").
KO_ROUND_TO_STAGE = [
    ("round of 32", "Ronda de 32"),
    ("round of 16", "Octavos"),
    ("quarter", "Cuartos"),
    ("semi", "Semifinales"),
    ("3rd place", "Tercer Puesto"),
    ("third place", "Tercer Puesto"),
    ("final", "Final"),  # debe ir último: "semi-final" ya matcheó arriba
]


def ko_stage_for_round(api_round):
    r = (api_round or "").lower()
    for needle, stage in KO_ROUND_TO_STAGE:
        if needle in r:
            return stage
    return None


def build_ko_fixture_to_slot(fixtures, my_matches):
    """Mapa fixture-id (API) -> id de cupo de llave (R32_01, ...).

    Empareja por etapa y ORDEN cronológico: los partidos de cada ronda en la API,
    ordenados por fecha, se zipean con mis cupos de esa etapa ordenados por hora.
    Ambos vienen del calendario oficial, así que el orden coincide. Si la cantidad
    no calza, se omite esa etapa (mejor dejar la etiqueta que adivinar mal).
    """
    my_by_stage = defaultdict(list)
    for m in my_matches:
        if not (isinstance(m.get("homeCode"), str) and isinstance(m.get("awayCode"), str)):
            my_by_stage[m.get("stage")].append(m)
    for v in my_by_stage.values():
        v.sort(key=lambda m: m.get("kickoff", ""))

    api_by_stage = defaultdict(list)
    for f in fixtures:
        stage = ko_stage_for_round((f.get("league") or {}).get("round"))
        if stage:
            api_by_stage[stage].append(f)
    for v in api_by_stage.values():
        v.sort(key=lambda f: (f.get("fixture") or {}).get("timestamp") or 0)

    out = {}
    for stage, slots in my_by_stage.items():
        apis = api_by_stage.get(stage, [])
        if not apis:
            continue
        if len(apis) != len(slots):
            print("AVISO llaves %s: API trae %d, espero %d; no mapeo esta etapa."
                  % (stage, len(apis), len(slots)))
            continue
        for f, slot in zip(apis, slots):
            out[str((f.get("fixture") or {}).get("id"))] = slot["id"]
    return out

RANKINGS = {
    "ARG": 1, "ESP": 2, "FRA": 3, "ENG": 4, "POR": 5, "BRA": 6, "MAR": 7,
    "NED": 8, "BEL": 9, "GER": 10, "CRO": 11, "COL": 13, "MEX": 14, "SEN": 15,
    "URU": 16, "USA": 17, "JPN": 18, "SUI": 19, "IRN": 20, "TUR": 22, "ECU": 23,
    "AUT": 24, "KOR": 25, "AUS": 27, "ALG": 28, "EGY": 29, "CAN": 30, "NOR": 31,
    "CIV": 33, "PAN": 34, "SWE": 38, "CZE": 40, "PAR": 41, "SCO": 42, "TUN": 45,
    "COD": 46, "UZB": 50, "QAT": 56, "IRQ": 58, "RSA": 60, "KSA": 61, "JOR": 64,
    "CPV": 70, "GHA": 73, "BIH": 74, "HAI": 84, "CUR": 85, "NZL": 86,
}

NAME_TO_CODE = {
    "mexico": "MEX", "southafrica": "RSA", "southkorea": "KOR", "korearepublic": "KOR",
    "czechrepublic": "CZE", "czechia": "CZE", "canada": "CAN", "switzerland": "SUI",
    "bosniaherzegovina": "BIH", "bosniaandherzegovina": "BIH", "qatar": "QAT",
    "brazil": "BRA", "morocco": "MAR", "scotland": "SCO", "haiti": "HAI",
    "usa": "USA", "unitedstates": "USA", "australia": "AUS", "turkiye": "TUR",
    "turkey": "TUR", "paraguay": "PAR", "germany": "GER", "ivorycoast": "CIV",
    "cotedivoire": "CIV", "ecuador": "ECU", "curacao": "CUR", "netherlands": "NED",
    "sweden": "SWE", "japan": "JPN", "tunisia": "TUN", "belgium": "BEL",
    "iran": "IRN", "iriran": "IRN", "iranislamicrepublic": "IRN", "egypt": "EGY",
    "newzealand": "NZL", "spain": "ESP", "saudiarabia": "KSA", "uruguay": "URU",
    "capeverde": "CPV", "capeverdeislands": "CPV", "france": "FRA", "senegal": "SEN",
    "norway": "NOR", "iraq": "IRQ", "argentina": "ARG", "austria": "AUT",
    "algeria": "ALG", "jordan": "JOR", "portugal": "POR", "uzbekistan": "UZB",
    "colombia": "COL", "drcongo": "COD", "congodr": "COD", "democraticrepublicofcongo": "COD",
    "congodemocraticrepublic": "COD", "england": "ENG", "croatia": "CRO",
    "ghana": "GHA", "panama": "PAN",
}

_ACCENTS = str.maketrans({
    "á": "a", "à": "a", "â": "a", "ã": "a", "ä": "a", "é": "e", "è": "e", "ê": "e",
    "ë": "e", "í": "i", "ì": "i", "î": "i", "ï": "i", "ó": "o", "ò": "o", "ô": "o",
    "õ": "o", "ö": "o", "ú": "u", "ù": "u", "û": "u", "ü": "u", "ç": "c", "ñ": "n",
})


def norm(s):
    return re.sub(r"[^a-z]", "", (s or "").lower().translate(_ACCENTS))


def code_for(api_name):
    return NAME_TO_CODE.get(norm(api_name))


def api_get(key, path):
    req = urllib.request.Request(API + path, headers={"x-apisports-key": key})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def pair_key(a, b):
    return "|".join(sorted([a, b]))


def read_key():
    key = (os.environ.get("APIFOOTBALL_KEY") or "").strip()
    if key:
        return key
    for p in ("tool/api_key.txt", "api_key.txt"):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read().strip()
    return ""


def read_json(path):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def filter_events(raw):
    """Quedarse solo con goles y tarjetas, con los campos mínimos."""
    out = []
    for e in raw:
        t = e.get("type")
        if t not in ("Goal", "Card"):
            continue
        out.append({
            "type": t,
            "detail": e.get("detail") or "",
            "team": (e.get("team") or {}).get("name") or "",
            "player": (e.get("player") or {}).get("name") or "",
            "assist": (e.get("assist") or {}).get("name") or "",
            "minute": (e.get("time") or {}).get("elapsed"),
        })
    return out


def collect_events(key, played):
    """Eventos (goles/tarjetas) de cada partido jugado. Cachea los terminados."""
    cache = read_json(EVENTS_CACHE) or {}
    events_by_fid = {}
    new_cache = {}
    reqs = 0
    for f in played:
        fx = f.get("fixture") or {}
        fid = str(fx.get("id"))
        is_done = ((fx.get("status") or {}).get("short") or "") in DONE_STATUSES
        if is_done and fid in cache:
            evs = cache[fid]
        else:
            try:
                raw = api_get(key, "/fixtures/events?fixture=%s" % fid).get("response", [])
                evs = filter_events(raw)
                reqs += 1
            except Exception as e:
                print("AVISO events %s: %s" % (fid, e))
                evs = cache.get(fid, [])
        events_by_fid[fid] = evs
        if is_done:
            new_cache[fid] = evs
    try:
        os.makedirs("live", exist_ok=True)
        with open(EVENTS_CACHE, "w", encoding="utf-8") as f:
            json.dump(new_cache, f, ensure_ascii=False)
    except Exception as e:
        print("AVISO guardando cache:", e)
    return events_by_fid, reqs


def cards_by_team(evs, home_api, away_api):
    """(amarillas, rojas) de local y visita a partir de los eventos."""
    hy = hr = ay = ar = 0
    hn, an = norm(home_api), norm(away_api)
    for e in evs:
        if e["type"] != "Card":
            continue
        is_red = "Red" in e["detail"] or "Second Yellow" in e["detail"]
        is_yellow = e["detail"] == "Yellow Card"
        tm = norm(e["team"])
        if tm == hn:
            hr += 1 if is_red else 0
            hy += 1 if is_yellow else 0
        elif tm == an:
            ar += 1 if is_red else 0
            ay += 1 if is_yellow else 0
    return hy, hr, ay, ar


def match_events(evs):
    """Goleadores y amonestados de UN partido, con nombre y minuto, en el
    formato que consume la app (scorers[]/bookings[] dentro del result).

    Los goles incluyen autogoles y penales (con su `detail` para que la app los
    marque); el `teamCode` es el del equipo al que se le acredita el evento, así
    la app lo ubica del lado correcto. Se ordenan por minuto.
    """
    scorers, bookings = [], []
    for e in evs:
        name = e.get("player") or ""
        if not name:
            continue
        item = {"name": name, "teamCode": code_for(e.get("team", "")) or "",
                "detail": e.get("detail", "")}
        minute = e.get("minute")
        if isinstance(minute, int):
            item["minute"] = minute
        if e.get("type") == "Goal":
            scorers.append(item)
        elif e.get("type") == "Card":
            bookings.append(item)
    scorers.sort(key=lambda x: x.get("minute", 999))
    bookings.sort(key=lambda x: x.get("minute", 999))
    return scorers, bookings


def build_scorers_and_bookings(events_by_fid):
    goals = defaultdict(int)
    assists = defaultdict(int)
    yellow = defaultdict(int)
    red = defaultdict(int)
    team_of = {}
    for evs in events_by_fid.values():
        for e in evs:
            tm = e["team"]
            if e["type"] == "Goal" and e["detail"] != "Own Goal":
                p = e["player"]
                if p:
                    goals[p] += 1
                    team_of[p] = tm
                a = e["assist"]
                if a:
                    assists[a] += 1
                    team_of.setdefault(a, tm)
            elif e["type"] == "Card":
                p = e["player"]
                if not p:
                    continue
                team_of.setdefault(p, tm)
                if "Red" in e["detail"] or "Second Yellow" in e["detail"]:
                    red[p] += 1
                elif e["detail"] == "Yellow Card":
                    yellow[p] += 1

    scorers = sorted(
        ({"name": p, "teamCode": code_for(team_of.get(p, "")) or "",
          "goals": g, "assists": assists.get(p, 0)} for p, g in goals.items()),
        key=lambda x: (-x["goals"], -x["assists"], x["name"]))
    top = [{"rank": i, "name": s["name"], "teamCode": s["teamCode"], "club": "",
            "league": "", "position": "", "goals": s["goals"],
            "assists": s["assists"], "matches": 0}
           for i, s in enumerate(scorers, 1)]

    booked = sorted(
        ({"name": p, "teamCode": code_for(team_of.get(p, "")) or "",
          "yellow": yellow.get(p, 0), "red": red.get(p, 0)}
         for p in set(list(yellow) + list(red))),
        key=lambda x: (-x["red"], -x["yellow"], x["name"]))
    bookings = [{"rank": i, **b} for i, b in enumerate(booked, 1)]
    return top, bookings


def main():
    key = read_key()
    if not key:
        raise SystemExit("ERROR: falta la API key (env APIFOOTBALL_KEY o tool/api_key.txt).")

    my_matches = (read_json(MATCHES_PATH) or {}).get("matches", [])
    my_by_id = {m.get("id"): m for m in my_matches}
    pair_to_match = {}
    for m in my_matches:
        h, a = m.get("homeCode"), m.get("awayCode")
        if isinstance(h, str) and isinstance(a, str):
            pair_to_match[pair_key(h, a)] = m

    try:
        fixtures = api_get(key, "/fixtures?league=%s&season=%s" % (LEAGUE, SEASON)).get("response", [])
    except Exception as e:
        print("AVISO: no se pudieron leer fixtures:", e)
        fixtures = []

    # Mapa de los partidos de eliminatoria de la API a mis cupos de llave.
    ko_fixture_to_slot = build_ko_fixture_to_slot(fixtures, my_matches)

    played = [f for f in fixtures
              if (((f.get("fixture") or {}).get("status") or {}).get("short") or "")
              in (LIVE_STATUSES | DONE_STATUSES)]
    events_by_fid, reqs = collect_events(key, played) if played else ({}, 0)

    results = {}
    mapped = unmapped = live_count = 0
    for f in fixtures:
        fx = f.get("fixture") or {}
        status = ((fx.get("status") or {}).get("short") or "")
        is_done = status in DONE_STATUSES
        is_live = status in LIVE_STATUSES
        if not (is_done or is_live):
            continue
        teams = f.get("teams") or {}
        goals = f.get("goals") or {}
        hname = (teams.get("home") or {}).get("name") or ""
        aname = (teams.get("away") or {}).get("name") or ""
        hc, ac = code_for(hname), code_for(aname)
        hg, ag = goals.get("home"), goals.get("away")
        if hc is None or ac is None or not isinstance(hg, int) or not isinstance(ag, int):
            unmapped += 1
            continue
        m = pair_to_match.get(pair_key(hc, ac))
        is_ko = False
        if m is None:
            # ¿Es un partido de eliminatoria? Lo ubicamos por su cupo de llave.
            slot_id = ko_fixture_to_slot.get(str(fx.get("id")))
            m = my_by_id.get(slot_id) if slot_id else None
            if m is None:
                unmapped += 1
                continue
            is_ko = True
        # En eliminatoria mi cupo no tiene local fijo: uso el orden de la API.
        home_is_api_home = True if is_ko else (m["homeCode"] == hc)
        entry = {
            "homeGoals": hg if home_is_api_home else ag,
            "awayGoals": ag if home_is_api_home else hg,
            "finished": is_done,
        }
        if is_ko:
            # Equipos reales que avanzaron (la app los pinta en el cuadro).
            entry["homeCode"] = hc if home_is_api_home else ac
            entry["awayCode"] = ac if home_is_api_home else hc
        evs = events_by_fid.get(str(fx.get("id")), [])
        hy, hr, ay, ar = cards_by_team(evs, hname, aname)
        if not home_is_api_home:
            hy, hr, ay, ar = ay, ar, hy, hr
        entry["homeYellow"], entry["homeRed"] = hy, hr
        entry["awayYellow"], entry["awayRed"] = ay, ar
        # Goleadores y amonestados con NOMBRE de este partido.
        sc, bk = match_events(evs)
        if sc:
            entry["scorers"] = sc
        if bk:
            entry["bookings"] = bk
        if is_live:
            entry["live"] = True
            entry["statusShort"] = status
            elapsed = (fx.get("status") or {}).get("elapsed")
            if isinstance(elapsed, int):
                entry["minute"] = elapsed
            live_count += 1
        results[m["id"]] = entry
        mapped += 1

    top, bookings = build_scorers_and_bookings(events_by_fid)

    payload = {"rankings": RANKINGS, "results": results,
               "topScorers": top, "bookings": bookings}
    old = read_json(OUT_PATH) or {}
    if {"rankings": old.get("rankings"), "results": old.get("results"),
            "topScorers": old.get("topScorers"),
            "bookings": old.get("bookings")} == payload:
        print("sin cambios relevantes (%d resultados, %d en vivo, %d req eventos); no se reescribe."
              % (len(results), live_count, reqs))
        return

    out = {
        "_comment": "Feed EN VIVO del Mundial 2026. Generado por update_feed.py con API-Football.",
        "updatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        **payload,
    }
    os.makedirs("live", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("live/live.json: %d resultados (%d en vivo), %d goleadores, %d amonestados "
          "(%d req eventos, mapeados %d, sin mapear %d)."
          % (len(results), live_count, len(top), len(bookings), reqs, mapped, unmapped))


if __name__ == "__main__":
    main()
