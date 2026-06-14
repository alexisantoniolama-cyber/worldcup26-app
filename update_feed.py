"""Actualizador del feed EN VIVO del Mundial 2026 (live/live.json).

Corre en GitHub Actions en bucle (ver update-feed.yml). Consulta API-Football y
reescribe live/live.json con:
  - results:    marcador de partidos TERMINADOS y EN VIVO, mapeados al id del
                fixture propio (M001, ...). Los en vivo llevan finished:false,
                live:true, statusShort, minute, y tarjetas por equipo
                (homeYellow/homeRed/awayYellow/awayRed).
  - rankings:   ranking FIFA (fijo; la API no da el ranking mundial).
  - topScorers: goleadores del torneo.
  - bookings:   amonestados del torneo (amarillas/rojas por jugador).

Para no gastar cuota: el marcador se pide siempre (1 request); goleadores,
amonestados y eventos (tarjetas) solo se piden si hay algun partido EN VIVO o si
cambio algun resultado; si no, se reusan los del feed anterior.

La API key vive como secret APIFOOTBALL_KEY (o tool/api_key.txt en local), nunca
en el APK. Sin dependencias externas: solo la libreria estandar de Python.
"""
import datetime
import json
import os
import re
import urllib.request

API = "https://v3.football.api-sports.io"
LEAGUE = "1"
SEASON = "2026"
MATCHES_PATH = "live/matches.json"
SCORERS_PATH = "live/scorers.json"
OUT_PATH = "live/live.json"

LIVE_STATUSES = {"1H", "2H", "HT", "ET", "BT", "P", "LIVE", "INT", "SUSP"}
DONE_STATUSES = {"FT", "AET", "PEN"}

# Ranking FIFA (11 jun 2026). La API no da el ranking mundial. Proxima
# actualizacion FIFA: ~20 jul 2026 -> editar aqui.
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


def pos_es(api_pos):
    return {
        "Goalkeeper": "Arquero", "Defender": "Defensa",
        "Midfielder": "Mediocampista", "Attacker": "Delantero",
    }.get(api_pos, api_pos or "")


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


def read_old():
    if os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def count_cards(events, home_api, away_api):
    """Cuenta amarillas/rojas por equipo en los eventos de un partido.
    Doble amarilla ('Second Yellow card') cuenta como roja."""
    hy = hr = ay = ar = 0
    hn, an = norm(home_api), norm(away_api)
    for e in events:
        if e.get("type") != "Card":
            continue
        detail = (e.get("detail") or "")
        team = norm((e.get("team") or {}).get("name") or "")
        is_red = "Red" in detail or "Second Yellow" in detail
        is_yellow = detail == "Yellow Card"
        if team == hn:
            hr += 1 if is_red else 0
            hy += 1 if is_yellow else 0
        elif team == an:
            ar += 1 if is_red else 0
            ay += 1 if is_yellow else 0
    return hy, hr, ay, ar


def build_results(key):
    """Marcador (y tarjetas de los partidos en vivo) mapeado a nuestros ids."""
    with open(MATCHES_PATH, encoding="utf-8") as f:
        my_matches = json.load(f)["matches"]
    pair_to_match = {}
    for m in my_matches:
        h, a = m.get("homeCode"), m.get("awayCode")
        if isinstance(h, str) and isinstance(a, str):
            pair_to_match[pair_key(h, a)] = m

    results = {}
    mapped = unmapped = live_count = 0
    data = api_get(key, "/fixtures?league=%s&season=%s" % (LEAGUE, SEASON))
    for f in data.get("response", []):
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
        if m is None:
            unmapped += 1
            continue
        home_is_api_home = (m["homeCode"] == hc)
        entry = {
            "homeGoals": hg if home_is_api_home else ag,
            "awayGoals": ag if home_is_api_home else hg,
            "finished": is_done,
        }
        if is_live:
            entry["live"] = True
            entry["statusShort"] = status
            elapsed = (fx.get("status") or {}).get("elapsed")
            if isinstance(elapsed, int):
                entry["minute"] = elapsed
            # Tarjetas: solo para los partidos en vivo (pocos => pocas requests).
            try:
                evs = api_get(key, "/fixtures/events?fixture=%s" % fx.get("id")).get("response", [])
                hy, hr, ay, ar = count_cards(evs, hname, aname)
                entry["homeYellow"] = hy if home_is_api_home else ay
                entry["homeRed"] = hr if home_is_api_home else ar
                entry["awayYellow"] = ay if home_is_api_home else hy
                entry["awayRed"] = ar if home_is_api_home else hr
            except Exception as e:
                print("AVISO eventos %s: %s" % (fx.get("id"), e))
            live_count += 1
        results[m["id"]] = entry
    return results, mapped, unmapped, live_count


def build_top_scorers(key):
    top = []
    if os.path.exists(SCORERS_PATH):
        try:
            with open(SCORERS_PATH, encoding="utf-8") as f:
                lst = json.load(f)
            lst.sort(key=lambda s: (s.get("goals") or 0), reverse=True)
            for rank, s in enumerate(lst, 1):
                top.append({
                    "rank": rank, "name": str(s.get("name", "")),
                    "teamCode": str(s.get("teamCode", "")), "club": str(s.get("club", "")),
                    "league": str(s.get("league", "")), "position": str(s.get("position", "")),
                    "goals": int(s.get("goals") or 0), "assists": int(s.get("assists") or 0),
                    "matches": int(s.get("matches") or 0),
                })
            return top
        except Exception as e:
            print("AVISO scorers.json:", e)
    try:
        data = api_get(key, "/players/topscorers?league=%s&season=%s" % (LEAGUE, SEASON))
        rank = 0
        for p in data.get("response", []):
            player = p.get("player") or {}
            stats = p.get("statistics") or []
            if not stats:
                continue
            st = stats[0]
            goals_total = (st.get("goals") or {}).get("total")
            if not isinstance(goals_total, int) or goals_total <= 0:
                continue
            rank += 1
            assists = (st.get("goals") or {}).get("assists")
            apps = (st.get("games") or {}).get("appearences")
            top.append({
                "rank": rank, "name": str(player.get("name", "")),
                "teamCode": code_for((st.get("team") or {}).get("name") or "") or "",
                "club": "", "league": "",
                "position": pos_es((st.get("games") or {}).get("position")),
                "goals": goals_total,
                "assists": assists if isinstance(assists, int) else 0,
                "matches": apps if isinstance(apps, int) else 0,
            })
    except Exception as e:
        print("AVISO topscorers API:", e)
    return top


def build_bookings(key):
    """Amonestados del torneo: une topyellowcards + topredcards por jugador."""
    agg = {}  # (name, code) -> {yellow, red}
    for path in ("/players/topyellowcards", "/players/topredcards"):
        try:
            data = api_get(key, "%s?league=%s&season=%s" % (path, LEAGUE, SEASON))
        except Exception as e:
            print("AVISO %s: %s" % (path, e))
            continue
        for p in data.get("response", []):
            player = p.get("player") or {}
            stats = p.get("statistics") or []
            if not stats:
                continue
            st = stats[0]
            cards = st.get("cards") or {}
            yellow = int(cards.get("yellow") or 0)
            yellowred = int(cards.get("yellowred") or 0)
            red = int(cards.get("red") or 0) + yellowred  # doble amarilla = roja
            name = str(player.get("name", ""))
            code = code_for((st.get("team") or {}).get("name") or "") or ""
            if not name or (yellow == 0 and red == 0):
                continue
            agg[(name, code)] = {"yellow": yellow, "red": red}
    items = [
        {"name": n, "teamCode": c, "yellow": v["yellow"], "red": v["red"]}
        for (n, c), v in agg.items()
    ]
    # Mas rojas primero, luego mas amarillas.
    items.sort(key=lambda x: (x["red"], x["yellow"]), reverse=True)
    for rank, it in enumerate(items, 1):
        it["rank"] = rank
    return items


def core_results(results):
    """Solo marcador/fin (sin minuto ni tarjetas) para detectar cambios reales."""
    return {k: (v.get("homeGoals"), v.get("awayGoals"), v.get("finished"))
            for k, v in results.items()}


def main():
    key = read_key()
    if not key:
        raise SystemExit("ERROR: falta la API key (env APIFOOTBALL_KEY o tool/api_key.txt).")

    old = read_old()
    try:
        results, mapped, unmapped, live_count = build_results(key)
    except Exception as e:
        print("AVISO: no se pudieron leer fixtures de la API:", e)
        results, mapped, unmapped, live_count = {}, 0, 0, 0

    # Goleadores/amonestados: pedirlos solo si hay algo en vivo, cambio un
    # resultado, o el feed viejo aun no los trae; si no, reusar (ahorra cuota).
    changed = core_results(results) != core_results(old.get("results") or {})
    if live_count > 0 or changed or "topScorers" not in old or "bookings" not in old:
        top = build_top_scorers(key)
        bookings = build_bookings(key)
    else:
        top = old.get("topScorers") or []
        bookings = old.get("bookings") or []

    payload = {"rankings": RANKINGS, "results": results,
               "topScorers": top, "bookings": bookings}

    if {"rankings": old.get("rankings"), "results": old.get("results"),
            "topScorers": old.get("topScorers"),
            "bookings": old.get("bookings")} == payload:
        print("sin cambios relevantes (%d resultados, %d en vivo); no se reescribe."
              % (len(results), live_count))
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
    print("live/live.json escrito: %d resultados (%d en vivo), %d goleadores, %d amonestados "
          "(mapeados %d, sin mapear %d)." % (len(results), live_count, len(top), len(bookings), mapped, unmapped))


if __name__ == "__main__":
    main()
