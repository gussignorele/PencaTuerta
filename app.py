from flask import Flask, render_template, request, redirect, session, flash
import sqlite3
import os
from werkzeug.security import generate_password_hash, check_password_hash
from lib import calcular_puntos, logos, recalcular_ranking
from datetime import datetime

app = Flask(__name__)
app.secret_key = "super-secret-key-key"

DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")
UPLOAD_FOLDER = os.path.join("static", "img")

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

import os

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


def get_db():
    return sqlite3.connect(DB_PATH)


# =========================
# INIT DB
# =========================
def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        local TEXT,
        visitante TEXT,
        fecha_hora TEXT,
        fecha_num INTEGER,
        goles_local INTEGER,
        goles_visitante INTEGER
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS prediction (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user TEXT,
        match_id INTEGER,
        pred_local INTEGER,
        pred_visitante INTEGER,
        UNIQUE(user, match_id)
    )
    """)
    cursor.execute("""
                   CREATE TABLE IF NOT EXISTS scores
                   (
                       user
                       TEXT
                       PRIMARY
                       KEY,
                       puntos
                       INTEGER
                   )
                   """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        avatar TEXT,
        telefono TEXT
    )
    """)

    conn.commit()
    conn.close()


init_db()


# =========================
# HELPERS
# =========================
def parse_int(value, default=0):
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def is_admin():
    return session.get("is_admin") == True
@app.route("/user/<username>")
def user_detail(username):
    conn = get_db()
    cursor = conn.cursor()

    # avatar
    cursor.execute("SELECT avatar FROM users WHERE username=?", (username,))
    row = cursor.fetchone()
    avatar = row[0] if row else "default_m.png"

    # fecha seleccionada
    fecha_sel = request.args.get("fecha", type=int)

    if not fecha_sel:
        cursor.execute("SELECT MAX(fecha_num) FROM matches")
        fecha_sel = cursor.fetchone()[0]

    # lista de fechas
    cursor.execute("SELECT DISTINCT fecha_num FROM matches ORDER BY fecha_num")
    fechas = [f[0] for f in cursor.fetchall()]

    # traer datos
    cursor.execute("""
        SELECT m.local, m.visitante,
               m.goles_local, m.goles_visitante,
               p.pred_local, p.pred_visitante,
               m.fecha_hora
        FROM prediction p
        JOIN matches m ON p.match_id = m.id
        WHERE p.user = ?
          AND m.fecha_num = ?
        ORDER BY m.fecha_hora
    """, (username, fecha_sel))

    rows = cursor.fetchall()

    from datetime import datetime
    now = datetime.now()

    usuario_logueado = session.get("user")

    rows_con_puntos = []

    for r in rows:
        local, visitante, gl, gv, pl, pv, fecha_hora = r

        # 🔥 FIX: convertir a datetime
        fecha_dt = None
        if fecha_hora:
            try:
                fecha_dt = datetime.fromisoformat(fecha_hora)
            except:
                fecha_dt = None  # por si viene raro

        # ocultar si no empezó
        oculto = False
        if fecha_dt and fecha_dt > now and username != usuario_logueado:
            pl, pv = None, None
            oculto = True

        pts = calcular_puntos(gl, gv, pl, pv) if gl is not None else None

        rows_con_puntos.append(
            (local, visitante, gl, gv, pl, pv, pts, oculto)
        )

    conn.close()

    total_puntos = sum(p for *_, p, _ in rows_con_puntos if p is not None)

    return render_template(
        "user_detail.html",
        rows=rows_con_puntos,
        username=username,
        avatar=avatar,
        user=session.get("user"),
        logos=logos,
        fechas=fechas,
        fecha_sel=fecha_sel,
        total_puntos=total_puntos
    )
# =========================
# LOGIN
# =========================
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        username = request.form["username"].strip().lower()
        password = request.form["password"]

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM users WHERE username=?", (username,))
        user = cursor.fetchone()

        conn.close()

        if user and check_password_hash(user[2], password):
            session["user"] = username
            session["is_admin"] = False
            return redirect("/matches")

        flash("Usuario o contraseña incorrectos", "error")
        return redirect("/")

    return render_template("index.html")


# =========================
# ADMIN LOGIN
# =========================
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        password = request.form.get("password")

        if password == ADMIN_PASSWORD:
            session["is_admin"] = True
            return redirect("/admin")
        else:
            flash("Clave admin incorrecta", "error")

    return render_template("admin_login.html")


# =========================
# REGISTER
# =========================
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip().lower()
        password = request.form["password"]
        if len(password) < 8:
            flash("La contraseña debe tener al menos 8 caracteres", "error")
            return redirect("/register")


        telefono = request.form.get("telefono")

        if not telefono or len(telefono) < 7:
            flash("Teléfono inválido", "error")
            return redirect("/register")

        avatar_tipo = request.form.get("avatar_tipo")

        hashed_password = generate_password_hash(password)
        avatar = "default_m.png"

        avatar_map = {
            "m": "default_m.png",
            "f": "default_f.png",
            "f2": "default_f_dark.png",
            "m_old": "default_m_old.png",
            "f_old": "default_f_old.png",
            "nb": "default_nb.png"
        }
        avatar = avatar_map.get(avatar_tipo, "default_m.png")

        file = request.files.get("avatar_file")
        if file and file.filename != "":
            filename = f"{username}.png"
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            file.save(filepath)
            avatar = filename

        conn = get_db()
        cursor = conn.cursor()

        try:
            cursor.execute(
                "INSERT INTO users (username, password, avatar, telefono) VALUES (?, ?, ?, ?)",
                (username, hashed_password, avatar, telefono)
            )

            # 🔥 CREAR SCORE AUTOMÁTICO
            cursor.execute(
                "INSERT INTO scores (user, puntos) VALUES (?, 0)",
                (username,)
            )

            conn.commit()
        except:
            conn.close()
            flash("El usuario ya existe", "error")
            return redirect("/register")

        conn.close()
        session["user"] = username
        session["is_admin"] = False
        return redirect("/matches")

    return render_template("register.html")


# =========================
# MATCHES
# =========================
@app.route("/matches")
@app.route("/matches/<int:fecha_sel>")
def matches(fecha_sel=None):
    if "user" not in session:
        return redirect("/")

    user = session["user"]

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT MIN(fecha_num)
        FROM matches
        WHERE fecha_hora > ?
    """, (datetime.now().isoformat(),))

    row = cursor.fetchone()
    fecha_actual = row[0] if row else None

    if fecha_sel:
        fecha_actual = fecha_sel

    if fecha_actual is None:
        cursor.execute("SELECT MAX(fecha_num) FROM matches")
        fecha_actual = cursor.fetchone()[0]

    cursor.execute("SELECT MIN(fecha_num), MAX(fecha_num) FROM matches")
    min_fecha, max_fecha = cursor.fetchone()

    cursor.execute("""
        SELECT MIN(fecha_num)
        FROM matches
        WHERE fecha_hora > ?
    """, (datetime.now().isoformat(),))

    futura = cursor.fetchone()[0]
    fecha_max_permitida = futura if futura else fecha_actual
    if futura and fecha_actual > futura:
        fecha_actual = futura

    cursor.execute("""
        SELECT m.*, p.pred_local, p.pred_visitante
        FROM matches m
        LEFT JOIN prediction p
            ON m.id = p.match_id AND p.user = ?
        WHERE m.fecha_num = ?
        ORDER BY m.fecha_hora
    """, (user, fecha_actual))

    matches_data = cursor.fetchall()

    conn.close()

    return render_template(
        "matches.html",
        matches=matches_data,
        user=user,
        logos=logos,
        fecha_actual=fecha_actual,
        min_fecha=min_fecha,
        max_fecha=max_fecha,
        fecha_max_permitida=fecha_max_permitida,
        now=datetime.now().isoformat()
    )


# =========================
# PREDICT
# =========================
@app.route("/predict", methods=["POST"])
def predict():
    if "user" not in session:
        return redirect("/")

    user = session["user"]
    match_id = int(request.form["match_id"])

    pl = request.form.get("pred_local")
    pv = request.form.get("pred_visitante")

    # ❌ evitar guardar vacíos como 0
    if pl == "" or pv == "":
        flash("Ingresá ambos goles", "error")
        return redirect(request.referrer or "/matches")

    pred_local = int(pl)
    pred_visitante = int(pv)

    conn = get_db()
    cursor = conn.cursor()

    # 🔒 BLOQUEO: no permitir si el partido ya empezó
    cursor.execute("SELECT fecha_hora FROM matches WHERE id=?", (match_id,))
    row = cursor.fetchone()

    if row:
        fecha_partido = datetime.fromisoformat(row[0])

        if datetime.now() >= fecha_partido:
            conn.close()
            flash("El partido ya comenzó", "error")
            return redirect(request.referrer or "/matches")

    # guardar predicción
    cursor.execute("""
        UPDATE prediction
        SET pred_local=?, pred_visitante=?
        WHERE user=? AND match_id=?
    """, (pred_local, pred_visitante, user, match_id))

    if cursor.rowcount == 0:
        cursor.execute("""
            INSERT INTO prediction (user, match_id, pred_local, pred_visitante)
            VALUES (?, ?, ?, ?)
        """, (user, match_id, pred_local, pred_visitante))

    conn.commit()
    conn.close()

    return redirect(request.referrer or "/matches")
# =========================
# RANKING
# =========================
@app.route("/ranking")
def ranking():
    if "user" not in session:
        return redirect("/")

    conn = get_db()
    cursor = conn.cursor()

    # 🔹 ranking general
    cursor.execute("""
        SELECT u.username, u.avatar, s.puntos
        FROM users u
        LEFT JOIN scores s ON u.username = s.user
    """)
    rows = cursor.fetchall()

    ranking = [
        (u, avatar, pts if pts is not None else 0)
        for u, avatar, pts in rows
    ]
    ranking = sorted(ranking, key=lambda x: (-x[2], x[0]))

    from datetime import datetime
    now = datetime.now()

    # 🔹 saber si la fecha global terminó (lo que ya tenías)
    cursor.execute("SELECT MAX(fecha_hora) FROM matches")
    ultima_fecha = cursor.fetchone()[0]

    fecha_jugada = False
    if ultima_fecha:
        try:
            fecha_dt = datetime.fromisoformat(ultima_fecha)
            fecha_jugada = now > fecha_dt
        except:
            pass

    # 🔹 fecha seleccionada
    fecha_sel = request.args.get("fecha", type=int)

    if not fecha_sel:
        cursor.execute("SELECT MAX(fecha_num) FROM matches")
        fecha_sel = cursor.fetchone()[0]

    # 🔹 estado de la fecha
    cursor.execute("""
        SELECT MAX(fecha_hora)
        FROM matches
        WHERE fecha_num = ?
    """, (fecha_sel,))
    fecha_max = cursor.fetchone()[0]

    fecha_estado = "En juego"
    if fecha_max:
        try:
            fecha_dt = datetime.fromisoformat(fecha_max)
            if now > fecha_dt:
                fecha_estado = "Finalizada"
        except:
            pass

    # 🔹 puntos por fecha
    pts_fecha = {}   # 🔥 IMPORTANTE (evita crash)

    cursor.execute("""
        SELECT p.user,
               SUM(
                   CASE
                       WHEN m.goles_local IS NOT NULL THEN
                           CASE
                               WHEN m.goles_local = p.pred_local AND m.goles_visitante = p.pred_visitante THEN 3
                               WHEN (m.goles_local - m.goles_visitante) *
                                    (p.pred_local - p.pred_visitante) > 0 THEN 1
                               ELSE 0
                           END
                       ELSE 0
                   END
               ) as pts
        FROM prediction p
        JOIN matches m ON p.match_id = m.id
        WHERE m.fecha_num = ?
        GROUP BY p.user
    """, (fecha_sel,))

    pts_fecha = {u: pts for u, pts in cursor.fetchall()}

    # 🔹 ganador
    ganador_fecha = None
    if pts_fecha:
        ganador_fecha = max(pts_fecha.items(), key=lambda x: x[1])[0]

    conn.close()  # 🔥 ahora sí, al final

    return render_template(
        "ranking.html",
        ranking=ranking,
        current_user=session.get("user"),
        user=session.get("user"),
        fecha_sel=fecha_sel,
        pts_fecha=pts_fecha,
        ganador_fecha=ganador_fecha,
        fecha_estado=fecha_estado,
        fecha_jugada=fecha_jugada
    )

# =========================
# ADMIN DASHBOARD
# =========================
@app.route("/admin")
def admin():
    if not is_admin():
        return redirect("/admin/login")

    conn = get_db()
    cursor = conn.cursor()

    # 🔥 traer usuarios
    cursor.execute("SELECT username FROM users ORDER BY username")
    users = [u[0] for u in cursor.fetchall()]

    conn.close()

    return render_template(
        "admin.html",
        user=session.get("user"),
        users=users
    )

@app.route("/admin/delete_user/<username>", methods=["POST"])
def delete_user(username):
    if not is_admin():
        return redirect("/")

    conn = get_db()
    cursor = conn.cursor()

    # borrar dependencias primero
    cursor.execute("DELETE FROM prediction WHERE user = ?", (username,))
    cursor.execute("DELETE FROM scores WHERE user = ?", (username,))
    cursor.execute("DELETE FROM users WHERE username = ?", (username,))

    conn.commit()
    conn.close()

    flash(f"Usuario {username} eliminado", "success")
    return redirect("/admin")  # o donde estés

@app.route("/admin/matches", methods=["GET", "POST"])
def admin_matches():
    if not is_admin():
        return redirect("/admin/login")

    conn = get_db()
    conn.row_factory = sqlite3.Row  # 🔥 CLAVE (evita problemas de índices)
    cursor = conn.cursor()

    # =========================
    # GET PARAM
    # =========================
    fecha_num_sel = request.args.get("fecha_num", type=int)

    # =========================
    # POST (guardar partido)
    # =========================
    if request.method == "POST":
        local = request.form["local"]
        visitante = request.form["visitante"]
        fecha = request.form["fecha"]
        hora = request.form["hora"]
        fecha_num = int(request.form["fecha_num"])

        fecha_dt = datetime.fromisoformat(f"{fecha}T{hora}")

        cursor.execute("""
            INSERT INTO matches (local, visitante, fecha_hora, fecha_num)
            VALUES (?, ?, ?, ?)
        """, (local, visitante, fecha_dt.isoformat(), fecha_num))

        conn.commit()

        conn.close()  # 🔥 importante cerrar antes del redirect
        return redirect(f"/admin/matches?fecha_num={fecha_num}")

    # =========================
    # GET (traer partidos)
    # =========================
    matches = []

    if fecha_num_sel is not None:
        cursor.execute("""
            SELECT * FROM matches
            WHERE fecha_num = ?
            ORDER BY fecha_hora
        """, (fecha_num_sel,))
        matches = cursor.fetchall()

    # =========================
    # equipos ya usados
    # =========================
    equipos_usados = set()

    if fecha_num_sel is not None:
        cursor.execute("""
            SELECT local, visitante
            FROM matches
            WHERE fecha_num = ?
        """, (fecha_num_sel,))

        for row in cursor.fetchall():
            equipos_usados.add(row["local"])
            equipos_usados.add(row["visitante"])

    # =========================
    # equipos disponibles
    # =========================
    equipos = [e for e in logos.keys() if e not in equipos_usados]
    equipos_disponibles = sorted(
        equipos,
        key=lambda x: (x != "Defensor Sporting", x)
    )

    conn.close()

    return render_template(
        "admin_matches.html",
        matches=matches,
        user=session.get("user"),
        logos=logos,
        equipos=equipos_disponibles,
        fecha_num_sel=fecha_num_sel
    )

# =========================
# ADMIN RESULTS
# =========================
# =========================
# ADMIN RESULTS
# =========================
@app.route("/admin/results", methods=["GET", "POST"])
def admin_results():
    if not is_admin():
        return redirect("/admin/login")

    conn = get_db()
    cursor = conn.cursor()

    # 🔥 fecha seleccionada
    fecha_sel = request.args.get("fecha", type=int) or request.form.get("fecha", type=int)

    if request.method == "POST":
        match_id = request.form["match_id"]

        gl = request.form.get("goles_local")
        gv = request.form.get("goles_visitante")

        if gl == "" or gv == "":
            flash("Ingresá ambos goles", "error")
            conn.close()
            return redirect(f"/admin/results?fecha={fecha_sel}")

        gl = int(gl)
        gv = int(gv)

        cursor.execute("""
            UPDATE matches
            SET goles_local=?, goles_visitante=?
            WHERE id=?
        """, (gl, gv, match_id))

        conn.commit()
        recalcular_ranking()

    # 🔥 lista de fechas
    cursor.execute("SELECT DISTINCT fecha_num FROM matches ORDER BY fecha_num")
    fechas = [f[0] for f in cursor.fetchall()]

    # 🔥 si no viene → última fecha
    if not fecha_sel:
        cursor.execute("SELECT MAX(fecha_num) FROM matches")
        fecha_sel = cursor.fetchone()[0]

    # 🔥 traer SOLO esa fecha
    cursor.execute("""
        SELECT * FROM matches
        WHERE fecha_num = ?
        ORDER BY fecha_hora
    """, (fecha_sel,))
    matches = cursor.fetchall()

    conn.close()

    return render_template(
        "admin_results.html",
        matches=matches,
        user=session.get("user"),
        logos=logos,
        fechas=fechas,
        fecha_sel=fecha_sel
    )

@app.route("/admin/reset_results", methods=["POST"])
def reset_results():
    if not is_admin():
        return redirect("/admin/login")

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE matches
        SET goles_local = NULL,
            goles_visitante = NULL
    """)

    conn.commit()

    # 🔥 CLAVE
    recalcular_ranking()

    conn.close()

    flash("Resultados borrados", "success")
    fecha = request.form.get("fecha")
    if fecha:
        return redirect(f"/admin/results?fecha={fecha}")
    else:
        return redirect("/admin/results")
# =========================
# LOGOUT
# =========================
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)