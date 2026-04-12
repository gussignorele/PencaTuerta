
logos = {
    "Defensor Sporting": "dsc.png",
    "Peñarol": "pen.png",
    "Nacional": "nac.png",
    "Danubio": "dan.png",
    "Liverpool": "liv.png",
    "Cerro": "cerro.png",
    "Cerro Largo": "cerroL.png",
    "Wanderers": "wan.png",
    "Torque": "tor.png",
    "Progreso": "prog.png",
    "Racing": "rac.png",
    "Juventud": "juv.png",
    "Deportivo Maldonado": "depo.png",
    "Central Español": "ce.png",
    "Boston River": "bos.png",
    "Albion": "albion.png"
}


import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")

def get_db():
    return sqlite3.connect(DB_PATH)


def obtener_partidos():
    import requests
    from datetime import datetime

    url = "https://site.api.espn.com/apis/v2/sports/soccer/uru.1/scoreboard"

    r = requests.get(url)

    if r.status_code != 200:
        print("ERROR HTTP:", r.status_code)
        return []

    data = r.json()

    partidos = []

    for e in data.get("events", []):
        try:
            comp = e["competitions"][0]
            equipos = comp["competitors"]

            local = equipos[0]["team"]["displayName"]
            visitante = equipos[1]["team"]["displayName"]

            fecha = e["date"]
            fecha_dt = datetime.fromisoformat(fecha.replace("Z", "+00:00"))

            partidos.append((local, visitante, fecha_dt))
        except:
            pass

    print("PARTIDOS:", partidos)
    return partidos

def calcular_puntos(gl, gv, pl, pv):
    # exacto
    if gl == pl and gv == pv:
        return 3

    # resultado (ganador o empate)
    real = gl - gv
    pred = pl - pv

    if (real > 0 and pred > 0) or (real < 0 and pred < 0) or (real == 0 and pred == 0):
        return 1

    return 0


def recalcular_ranking(conn):
    #conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
                   SELECT 
    p.user,
    p.match_id,
    m.goles_local,
    m.goles_visitante,
    p.pred_local,
    p.pred_visitante
FROM prediction p
JOIN matches m ON p.match_id = m.id
WHERE 
    m.goles_local IS NOT NULL
    AND m.goles_visitante IS NOT NULL
                   """)

    rows = cursor.fetchall()

    puntos = {}

    procesados = set()

    for user, match_id, gl, gv, pl, pv in rows:
        key = (user, match_id)

        if key in procesados:
            continue

        procesados.add(key)

        if user not in puntos:
            puntos[user] = 0

        puntos[user] += calcular_puntos(gl, gv, pl, pv)

    # limpiar tabla
    cursor.execute("DELETE FROM scores")
    cursor.execute("SELECT username FROM users")
    all_users = [u[0] for u in cursor.fetchall()]
    # insertar nuevos
    for user in all_users:
        pts = puntos.get(user, 0)
        cursor.execute(
            "INSERT INTO scores (user, puntos) VALUES (?, ?)",
            (user, pts)
        )

    conn.commit()
    #conn.close()