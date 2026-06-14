"""Actualizador del feed EN VIVO del Mundial 2026 (live/live.json).

Corre en GitHub Actions cada ~5 min. Consulta API-Football y reescribe
live/live.json con:
  - results:    marcadores de partidos TERMINADOS y EN VIVO, mapeados al id del
                fixture propio (M001, ...). Los en vivo llevan finished:false y
                ademas live/statusShort/minute para que la app pueda mostrar
                "EN VIVO 60'". (La app vieja ignora esos campos extra y muestra
                solo el marcador; la nueva los usa.)
  - rankings:   ranking FIFA (fijo; la API no da el ranking mundial).
  - topScorers: goleadores del torneo (de live/scorers.json si existe; si no, API).

La app descarga este JSON y superpone los marcadores sobre el fixture
empaquetado (que queda como respaldo offline).

La API key NO va en la app: vive como secret APIFOOTBALL_KEY en GitHub Actions
(o tool/api_key.txt para correrlo a mano), asi nadie la extrae del APK.
Sin dependencias externas: usa solo la libreria estandar de Python.
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

# Estados de API-Football: en curso vs. terminado.
LIVE_STATUSES = {"1H", "2H", "HT", "ET", "BT", "P", "LIVE", "INT", "SUSP"}
DONE_STATUSES = {"FT", "AET", "PEN"}

# Ranking FIFA (11 jun 2026). La API no da el ranking mundial, lo mantiene este
# script. Proxima actualizacion FIFA: ~20 jul 2026 -> editar aqui.
RANKINGS = {
    "ARG": 1, "ESP": 2, "FRA": 3, "ENG": 4, "POR": 5, "BRA": 6, "MAR": 7,
    "NED": 8, "BEL": 9, "GER": 10, "CRO": 11, "COL": 13, "MEX": 14, "SEN": 15,
    "URU": 16, "USA": 17, "JPN": 18, "SUI": 19, "IRN": 20, "TUR": 22, "ECU": 23,
    "AUT": 24, "KOR": 25, "AUS": 27, "ALG": 28, "EGY": 29, "CAN": 30, "NOR": 31,
    "CIV": 33, "PAN": 34, "SWE": 38, "CZE": 40, "PAR": 41, "SCO": 42, "TUN": 45,
    "COD": 46, "UZB": 50, "QAT": 56, "IRQ": 58, "RSA": 60, "KSA": 61, "JOR": 64,
    "CPV": 70, "GHA": 73, "BIH": 74, "HAI": 84, "CUR": 85, "NZL": 86,
}

# Nombre de la seleccion en la API (normalizado) -> codigo FIFA del proyecto.
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


def build_results(key, pair_to_match):
    """Marcadores de partidos terminados y en vivo, mapeados a nuestros ids."""
    results = {}
    mapped = unmapped = live_count = 0
    data = api_get(key, "/fixtures?league=%s&season=%s" % (LEAGUE, SEASON))
    for f in data.get("response", []):
        status = (((f.get("fixture") or {}).get("status") or {}).get("short") or "")
        is_done = status in DONE_STATUSES
        is_live = status in LIVE_STATUSES
        if not (is_done or is_live):
            continue  # programado / pospuesto / cancelado -> no es marcador
        teams = f.get("teams") or {}
        goals = f.get("goals") or {}
        hc = code_for((teams.get("home") or {}).get("name") or "")
        ac = code_for((teams.get("away") or {}).get("name") or "")
        hg, ag = goals.get("home"), goals.get("away")
        if hc is None or ac is None or not isinstance(hg, int) or not isinstance(ag, int):
            unmapped += 1
            continue
        m = pair_to_match.get(pair_key(hc, ac))
        if m is None:
            unmapped += 1  # probablemente eliminatoria (codigos nulos), se afina luego
            continue
        # Orientar los goles segun quien es local en NUESTRO fixture.
        home_goals = hg if m["homeCode"] == hc else ag
        away_goals = ag if m["homeCode"] == hc else hg
        entry = {"homeGoals": home_goals, "awayGoals": away_goals, "finished": is_done}
        if is_live:
            entry["live"] = True
            entry["statusShort"] = status
            elapsed = ((f.get("fixture") or {}).get("status") or {}).get("elapsed")
            if isinstance(elapsed, int):
                entry["minute"] = elapsed
            live_count += 1
        results[m["id"]] = entry
        mapped += 1
    return results, mapped, unmapped, live_count


def build_top_scorers(key):
    """Goleadores: preferir live/scorers.json (fuente gratis mas completa); si
    no existe, la API (que tarda horas en agregar los goles por jugador)."""
    top = []
    if os.path.exists(SCORERS_PATH):
        try:
            with open(SCORERS_PATH, encoding="utf-8") as f:
                lst = json.load(f)
            lst.sort(key=lambda s: (s.get("goals") or 0), reverse=True)
            for rank, s in enumerate(lst, 1):
                top.append({
                    "rank": rank,
                    "name": str(s.get("name", "")),
                    "teamCode": str(s.get("teamCode", "")),
                    "club": str(s.get("club", "")),
                    "league": str(s.get("league", "")),
                    "position": str(s.get("position", "")),
                    "goals": int(s.get("goals") or 0),
                    "assists": int(s.get("assists") or 0),
                    "matches": int(s.get("matches") or 0),
                })
            print("Goleadores desde live/scorers.json (fuente gratis): %d" % len(top))
            return top
        except Exception as e:
            print("AVISO: live/scorers.json invalido (%s); intento la API." % e)
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
                "rank": rank,
                "name": str(player.get("name", "")),
                "teamCode": code_for((st.get("team") or {}).get("name") or "") or "",
                "club": "",
                "league": "",
                "position": pos_es((st.get("games") or {}).get("position")),
                "goals": goals_total,
                "assists": assists if isinstance(assists, int) else 0,
                "matches": apps if isinstance(apps, int) else 0,
            })
    except Exception as e:
        print("AVISO: no se pudieron leer goleadores de la API:", e)
    return top


def main():
    key = read_key()
    if not key:
        raise SystemExit("ERROR: falta la API key (env APIFOOTBALL_KEY o tool/api_key.txt).")

    with open(MATCHES_PATH, encoding="utf-8") as f:
        my_matches = json.load(f)["matches"]
    pair_to_match = {}
    for m in my_matches:
        h, a = m.get("homeCode"), m.get("awayCode")
        if isinstance(h, str) and isinstance(a, str):
            pair_to_match[pair_key(h, a)] = m

    try:
        results, mapped, unmapped, live_count = build_results(key, pair_to_match)
    except Exception as e:
        print("AVISO: no se pudieron leer fixtures de la API:", e)
        results, mapped, unmapped, live_count = {}, 0, 0, 0

    top = build_top_scorers(key)

    payload = {"rankings": RANKINGS, "results": results, "topScorers": top}

    # Solo reescribir si cambio algo relevante (marcador, minuto, goleadores).
    # Asi el updatedAt y los commits reflejan cambios reales, no cada corrida.
    if os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, encoding="utf-8") as f:
                old = json.load(f)
            if {"rankings": old.get("rankings"), "results": old.get("results"),
                    "topScorers": old.get("topScorers")} == payload:
                print("sin cambios relevantes (%d resultados, %d en vivo); no se reescribe."
                      % (len(results), live_count))
                return
        except Exception:
            pass  # archivo ilegible -> reescribir

    out = {
        "_comment": "Feed EN VIVO del Mundial 2026. Generado por update_feed.py con API-Football.",
        "updatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        **payload,
    }
    os.makedirs("live", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("live/live.json escrito: %d resultados (%d en vivo), %d goleadores "
          "(mapeados %d, sin mapear %d)." % (len(results), live_count, len(top), mapped, unmapped))


if __name__ == "__main__":
    main()
