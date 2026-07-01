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


GROUP_LABEL_RE = re.compile(r"([12])\D*Grupo\s*([A-L])", re.IGNORECASE)


def build_group_qualifiers(standings_resp):
    """Devuelve (qualifiers, incompletos):
      - qualifiers: {'A': {1:'MEX', 2:'KOR', 3:..., 4:...}, ...} con el `rank`
        ACTUAL de la API (aplica los desempates oficiales). Incluye grupos en
        curso para poder PROYECTAR quién va 1°/2° según la tabla de hoy.
      - incompletos: set de letras de grupos que aún no terminaron (sus
        posiciones pueden cambiar -> la proyección es provisional).
    """
    out = {}
    incompletos = set()
    if not standings_resp:
        return out, incompletos
    tables = ((standings_resp[0] or {}).get("league") or {}).get("standings") or []
    for table in tables:
        if not table:
            continue
        gm = re.search(r"Group\s*([A-L])\b", table[0].get("group", "") or "", re.IGNORECASE)
        if not gm:
            continue  # pseudo-grupos (ranking de terceros, etc.) -> ignorar
        letter = gm.group(1).upper()
        if not all(((row.get("all") or {}).get("played") or 0) >= 3 for row in table):
            incompletos.add(letter)  # en curso -> proyección provisional
        ranks = {}
        for row in table:
            code = code_for((row.get("team") or {}).get("name", ""))
            r = row.get("rank")
            if code and isinstance(r, int):
                ranks[r] = code
        if ranks:
            out[letter] = ranks
    return out, incompletos


def resolve_group_label(label, group_qual):
    """'1° Grupo E' -> código del 1° del grupo E (si ese grupo ya terminó)."""
    m = GROUP_LABEL_RE.search(label or "")
    if not m:
        return None
    return (group_qual.get(m.group(2).upper()) or {}).get(int(m.group(1)))


# Etiqueta de avance de llave -> id de cupo del que sale el equipo.
# Ej: "Ganador R32-4" -> ganador del cupo R32_04; "Perdedor Semi-1" -> SF_01.
ADVANCE_RE = re.compile(r"(Ganador|Perdedor)\s+(R32|8vos|Cuartos|Semi)-(\d+)", re.IGNORECASE)
_ROUND_PREFIX_TO_ID = {"r32": "R32", "8vos": "R16", "cuartos": "QF", "semi": "SF"}


def resolve_advance_label(label, ko_result):
    """'Ganador R32-4' -> código del equipo que GANÓ el cupo R32_04 (o el que
    PERDIÓ, para el 3er puesto), si ese partido ya terminó. ko_result mapea
    id de cupo -> {'winner': code, 'loser': code}."""
    m = ADVANCE_RE.search(label or "")
    if not m:
        return None
    kind, rnd, num = m.group(1).lower(), m.group(2).lower(), int(m.group(3))
    slot_id = "%s_%02d" % (_ROUND_PREFIX_TO_ID[rnd], num)
    res = ko_result.get(slot_id)
    if not res:
        return None
    return res.get("winner") if kind == "ganador" else res.get("loser")


# Asignación de los MEJORES TERCEROS a su cupo de Ronda de 32, según la tabla
# oficial FIFA 2026. Depende de QUÉ 8 grupos clasifican con su tercero. Terminada
# la fase de grupos, los 8 mejores terceros salieron de los grupos B,D,E,F,I,J,K,L,
# y la tabla FIFA para esa combinación asigna (cupo -> letra del grupo del tercero):
#   1E(R32_02)->3D  1I(R32_05)->3F  1A(R32_07)->3E  1L(R32_08)->3K
#   1D(R32_09)->3B  1G(R32_10)->3I  1B(R32_13)->3J  1K(R32_15)->3L
# Guardamos solo la LETRA del grupo; el equipo concreto se lee de la tabla en vivo
# (group_qual[letra][3]), así no hardcodeamos códigos de equipo. Se aplica solo si
# ese grupo ya terminó y la letra es válida para el cupo (según su etiqueta '3° (...)').
THIRD_PLACE_SLOT_GROUP = {
    "R32_02": "D", "R32_05": "F", "R32_07": "E", "R32_08": "K",
    "R32_09": "B", "R32_10": "I", "R32_13": "J", "R32_15": "L",
}

# Letras de grupo elegibles para un cupo de tercero, p.ej. '2° (...)' no; '3° (C/E/F/H/I)' -> {C,E,F,H,I}
_THIRD_ELIGIBLE_RE = re.compile(r"3\D*\(([A-L/\s]+)\)")


def _third_eligible_groups(label):
    m = _THIRD_ELIGIBLE_RE.search(label or "")
    if not m:
        return None
    return {g.strip().upper() for g in m.group(1).split("/") if g.strip()}


def build_bracket(my_matches, group_qual, ko_result):
    """Cupos de llave resueltos: id -> {homeCode, awayCode}.
      - Lados '1°/2° Grupo X' desde la tabla (grupos ya terminados).
      - Lados 'Ganador/Perdedor <ronda>-N' desde el ganador/perdedor real del
        cupo anterior (avance inmediato, apenas termina cada llave; NO espera a
        que la API publique el fixture de la ronda siguiente).
      - Lados de MEJOR TERCERO ('3° (...)') desde la asignación oficial FIFA
        (THIRD_PLACE_SLOT_GROUP), leyendo el 3° de cada grupo de la tabla en vivo.
        Antes esto esperaba a que la API publicara el sorteo, y los partidos
        aparecían con un solo equipo."""
    out = {}
    for m in my_matches:
        if isinstance(m.get("homeCode"), str) and isinstance(m.get("awayCode"), str):
            continue  # partido de grupos (ya tiene equipos)
        slot = {}
        hc = (resolve_group_label(m.get("homeLabel"), group_qual)
              or resolve_advance_label(m.get("homeLabel"), ko_result))
        ac = (resolve_group_label(m.get("awayLabel"), group_qual)
              or resolve_advance_label(m.get("awayLabel"), ko_result))
        if ac is None:
            ac = resolve_best_third(m, group_qual)
        if hc:
            slot["homeCode"] = hc
        if ac:
            slot["awayCode"] = ac
        if slot:
            out[m["id"]] = slot
    return out


def resolve_best_third(m, group_qual):
    """Código del mejor tercero que ocupa el cupo de este partido (o None si el
    grupo asignado aún no terminó o la letra no es válida para el cupo)."""
    letter = THIRD_PLACE_SLOT_GROUP.get(m.get("id"))
    if not letter:
        return None
    eligible = _third_eligible_groups(m.get("awayLabel"))
    if eligible is not None and letter not in eligible:
        return None  # etiqueta y tabla FIFA no concuerdan -> no arriesgar
    return (group_qual.get(letter) or {}).get(3)


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
            # "Penalty Shootout" marca los penales de la TANDA (no suman al goleador).
            "comments": e.get("comments") or "",
        })
    return out


def _is_shootout(e):
    """Penal de la tanda definitoria (no cuenta como gol del jugador)."""
    return "shootout" in (e.get("comments") or "").lower()


def is_goal_shown(e):
    """Gol que se MUESTRA en el detalle del partido. Incluye autogol (la app lo
    marca con (ag)), pero excluye el penal ERRADO y los penales de la tanda
    definitoria, que API-Football entrega como type=Goal pero NO son gol en el
    marcador."""
    return (e.get("type") == "Goal"
            and e.get("detail") != "Missed Penalty"
            and not _is_shootout(e))


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
        if is_goal_shown(e):
            scorers.append(item)
        elif e.get("type") == "Card":
            bookings.append(item)
    scorers.sort(key=lambda x: x.get("minute", 999))
    bookings.sort(key=lambda x: x.get("minute", 999))
    return scorers, bookings


def shootout_kicks(evs):
    """Penales de la TANDA definitoria de UN partido, en orden de ejecución.

    Devuelve lista [{name, teamCode, scored}] (scored=False si erró) o [] si no
    hubo tanda. API-Football entrega cada penal de la tanda como type=Goal con
    comments="Penalty Shootout"; detail="Penalty" (convirtió) o "Missed Penalty"
    (falló). El orden de la lista respeta el orden de ejecución."""
    out = []
    for e in evs:
        if not _is_shootout(e) or e.get("type") != "Goal":
            continue
        out.append({
            "name": e.get("player") or "",
            "teamCode": code_for(e.get("team", "")) or "",
            "scored": e.get("detail") != "Missed Penalty",
        })
    return out


def build_scorers_and_bookings(events_by_fid):
    goals = defaultdict(int)
    assists = defaultdict(int)
    yellow = defaultdict(int)
    red = defaultdict(int)
    team_of = {}
    for evs in events_by_fid.values():
        for e in evs:
            tm = e["team"]
            if is_goal_shown(e) and e["detail"] != "Own Goal":
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
    ko_result = {}  # id de cupo de llave -> {'winner': code, 'loser': code}
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
            # Ganador/perdedor de la llave (para propagar a la ronda siguiente).
            # Se usa el flag winner de la API (respeta prórroga y penales); si no
            # viene, se cae al marcador. Solo con el partido TERMINADO.
            if is_done:
                home_win = (teams.get("home") or {}).get("winner")
                away_win = (teams.get("away") or {}).get("winner")
                if home_win is True:
                    win, lose = hc, ac
                elif away_win is True:
                    win, lose = ac, hc
                elif hg > ag:
                    win, lose = hc, ac
                elif ag > hg:
                    win, lose = ac, hc
                else:
                    win = lose = None
                if win:
                    ko_result[m["id"]] = {"winner": win, "loser": lose}
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
        # Definición por penales (solo eliminatoria terminada en tanda). El
        # marcador (homeGoals/awayGoals) queda como terminó el tiempo reglamentario
        # + prórroga; la tanda va aparte para que la app muestre quién ganó y quién
        # convirtió/erró cada penal.
        if is_ko and is_done:
            kicks = shootout_kicks(evs)
            if kicks:
                entry["penScorers"] = kicks
                entry["penHome"] = sum(
                    1 for k in kicks if k["scored"] and k["teamCode"] == entry["homeCode"])
                entry["penAway"] = sum(
                    1 for k in kicks if k["scored"] and k["teamCode"] == entry["awayCode"])
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

    # Cuadro de eliminatorias: 1° y 2° de cada grupo YA TERMINADO, desde la tabla
    # oficial de la API. Vacío hasta que cierren los grupos (se llena solo).
    try:
        standings = api_get(key, "/standings?league=%s&season=%s" % (LEAGUE, SEASON)).get("response", [])
    except Exception as e:
        print("AVISO standings:", e)
        standings = []
    group_qual, incompletos = build_group_qualifiers(standings)
    bracket = build_bracket(my_matches, group_qual, ko_result)
    # La proyección es PROVISIONAL si algún grupo que aporta al cuadro sigue en
    # curso (sus posiciones pueden cambiar en la última fecha). Cuando todos los
    # grupos terminan, queda confirmada (la app saca la etiqueta "proyección").
    bracket_provisional = bool(bracket) and len(incompletos) > 0

    payload = {"rankings": RANKINGS, "results": results, "bracket": bracket,
               "bracketProvisional": bracket_provisional,
               "topScorers": top, "bookings": bookings}
    old = read_json(OUT_PATH) or {}
    same = (old.get("rankings") == payload["rankings"]
            and old.get("results") == payload["results"]
            and (old.get("bracket") or {}) == payload["bracket"]
            and bool(old.get("bracketProvisional")) == bracket_provisional
            and old.get("topScorers") == payload["topScorers"]
            and old.get("bookings") == payload["bookings"])
    if same:
        print("sin cambios relevantes (%d resultados, %d en vivo, %d cupos llave, %d req); no se reescribe."
              % (len(results), live_count, len(bracket), reqs))
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
    print("live/live.json: %d resultados (%d en vivo), %d cupos de llave%s, %d goleadores, "
          "%d amonestados (%d req eventos, mapeados %d, sin mapear %d)."
          % (len(results), live_count, len(bracket),
             " (proyección)" if bracket_provisional else "",
             len(top), len(bookings), reqs, mapped, unmapped))


if __name__ == "__main__":
    main()
