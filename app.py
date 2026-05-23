from flask import Flask, render_template, request, redirect, session, flash
import sqlite3
import os
import time

from werkzeug.security import generate_password_hash, check_password_hash
from lib import calcular_puntos, logos, recalcular_ranking
from datetime import datetime
from PIL import Image
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import requests
import io
app = Flask(__name__)
#app.secret_key = "super-secret-key-key"
app.secret_key = os.getenv("SECRET_KEY", "dev-key")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["MAX_CONTENT_LENGTH"] = 3 * 1024 * 1024

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[]
)
DB_PATH = "/data/database.db" if os.path.exists("/data") else "database.db"

if os.path.exists("/data"):
    UPLOAD_FOLDER = "/data/avatars"
else:
    UPLOAD_FOLDER = os.path.join("static", "img")

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
ADMINS = {"gsignorele", "alejo iglesias", "pablo"}
PAYMENTS_ENABLED = os.getenv("PAYMENTS_ENABLED", "false").lower() == "true"
PRICE_PER_FECHA = int(os.getenv("PRICE_PER_FECHA", "150"))


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def puede_jugar(user, fecha):
    if user in ADMINS:
        return True
    if not PAYMENTS_ENABLED:
        return True
    return pago_habilitado(user, fecha)

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
    CREATE TABLE IF NOT EXISTS reset_tokens (
        email TEXT,
        token TEXT,
        created_at INTEGER
    )
    """)
    cursor.execute("""
                   CREATE TABLE IF NOT EXISTS payments
                   (
                       id
                       INTEGER
                       PRIMARY
                       KEY
                       AUTOINCREMENT,
                       user
                       TEXT,
                       fecha_num
                       INTEGER,
                       status
                       TEXT
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
    telefono TEXT,
    email TEXT
)
    """)

    conn.commit()
    conn.close()


init_db()

from flask import send_from_directory

@app.route('/avatars/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)



# =========================
# HELPERS
# =========================
def parse_int(value, default=0):
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def is_admin():
    user = session.get("user", "").strip().lower()
    return user in ADMINS or session.get("is_admin") == True

def pago_habilitado(user, fecha):
    user = user.strip().lower()

    if user in ADMINS:
        return True

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT 1 FROM payments
        WHERE user=? AND fecha_num=? AND status='approved'
    """, (user, fecha))

    ok = cursor.fetchone() is not None

    conn.close()
    return ok



@app.route("/crear_pago/<int:fecha>")
def crear_pago(fecha):
    import requests

    if "user" not in session:
        return redirect("/")

    user = session["user"]


    url = "https://api.mercadopago.com/checkout/preferences"
    BASE_URL = "https://penca-tuerta.onrender.com"
    payload = {

        "notification_url": "https://penca-tuerta.onrender.com/webhook",
        "items": [
            {
                "title": f"Penca Fecha {fecha}",
                "quantity": 1,
                "unit_price": PRICE_PER_FECHA  # 🔥 bajo para pruebas
            }
        ],
        "metadata": {
            "user": user,
            "fecha": fecha
        },

        "back_urls": {
            "success": f"{BASE_URL}/matches",
            "failure": f"{BASE_URL}/matches",
            "pending": f"{BASE_URL}/matches"
        },
        "auto_return": "approved"
    }

    headers = {
        "Authorization": f"Bearer {MP_ACCESS_TOKEN}"
    }

    r = requests.post(url, json=payload, headers=headers)

    data = r.json()

    return redirect(data["init_point"])
@app.route("/webhook", methods=["POST"])
def webhook():
    import requests
    import os

    data = request.json

    if data.get("type") == "payment":
        payment_id = data["data"]["id"]

        r = requests.get(
            f"https://api.mercadopago.com/v1/payments/{payment_id}",
            headers={"Authorization": f"Bearer {os.getenv('MP_ACCESS_TOKEN')}"}
        )

        payment = r.json()

        if payment["status"] == "approved":
            user = payment["metadata"]["user"]
            fecha = payment["metadata"]["fecha"]

            conn = get_db()
            cursor = conn.cursor()

            cursor.execute("""
                           SELECT 1
                           FROM payments
                           WHERE user = ?
                             AND fecha_num = ?
                           """, (user, fecha))

            if not cursor.fetchone():
                cursor.execute("""
                               INSERT INTO payments (user, fecha_num, status)
                               VALUES (?, ?, 'approved')
                               """, (user, fecha))

            conn.commit()
            conn.close()

    return "OK", 200
@app.route("/update_avatar", methods=["POST"])
def update_avatar():
    if "user" not in session:
        return redirect("/")

    user = session["user"]
    file = request.files.get("avatar_file")

    if not file or file.filename == "":
        return redirect(f"/user/{user}")

    filename = f"{user}.jpg"
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)

    from PIL import Image

    img = Image.open(file)
    img = img.convert("RGB")

    width, height = img.size
    min_side = min(width, height)

    left = (width - min_side) // 2
    top = (height - min_side) // 2
    right = (width + min_side) // 2
    bottom = (height + min_side) // 2

    img = img.crop((left, top, right, bottom))
    img = img.resize((200, 200))

    img.save(filepath, format="JPEG", quality=75, optimize=True)


    avatar = f"/avatars/{filename}?t={int(time.time())}"
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "UPDATE users SET avatar=? WHERE username=?",
        (avatar, user)
    )

    conn.commit()
    conn.close()
    flash("Avatar actualizado correctamente", "success")
    return redirect(f"/user/{user}")


@app.route("/admin/delete_match/<int:match_id>", methods=["POST"])
def delete_match(match_id):
    if not is_admin():
        return redirect("/admin/login")

    conn = get_db()
    cursor = conn.cursor()

    # borrar predicciones asociadas
    #cursor.execute("DELETE FROM prediction WHERE match_id=?", (match_id,))

    # borrar partido
    cursor.execute("DELETE FROM matches WHERE id=?", (match_id,))

    conn.commit()
    conn.close()

    return redirect(request.referrer or "/admin/matches")
@app.route("/fix_points")
def fix_points():
    if not is_admin():
        return redirect("/admin/login")
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE scores
        SET puntos = puntos + 3
        WHERE user='mariano'
    """)

    cursor.execute("""
        UPDATE scores
        SET puntos = puntos + 1
        WHERE user='rafael'
    """)

    cursor.execute("""
        UPDATE scores
        SET puntos = puntos + 1
        WHERE user='seba silva'
    """)

    conn.commit()
    conn.close()

    return "OK"
@app.route("/admin/reset_user_password", methods=["GET", "POST"])
def admin_reset_user_password():

    if session.get("user") != "gsignorele":
        return redirect("/")

    if request.method == "POST":

        username = request.form["username"].strip().lower()
        nueva = request.form["password"]

        if len(nueva) < 4:
            flash("Contraseña muy corta", "error")
            return redirect("/admin/reset_user_password")

        hashed = generate_password_hash(nueva)

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE users
            SET password = ?
            WHERE username = ?
        """, (hashed, username))

        conn.commit()
        conn.close()

        flash(f"Password cambiada para {username}", "success")

        return redirect("/admin")

    return """
    <h2>Reset Password Usuario</h2>

    <form method="POST">

        Usuario:<br>
        <input name="username"><br><br>

        Nueva password:<br>
        <input name="password"><br><br>

        <button type="submit">
            Cambiar
        </button>

    </form>
    """
@app.route("/user/<username>")
def user_detail(username):
    conn = get_db()
    cursor = conn.cursor()

    # avatar
    cursor.execute("SELECT avatar FROM users WHERE username=?", (username,))
    row = cursor.fetchone()
    avatar = row[0] if row else "/static/img/default_m.png"

    # fecha seleccionada
    fecha_sel = request.args.get("fecha", type=int)

    if not fecha_sel:
        cursor.execute("""
                       SELECT MAX(m.fecha_num)
                       FROM prediction p
                                JOIN matches m ON p.match_id = m.id
                       WHERE p.user = ?
                       """, (username,))

        row = cursor.fetchone()

        if row and row[0]:
            fecha_sel = row[0]
        else:
            fecha_sel = 1

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
    now = now_uy()

    usuario_logueado = session.get("user")

    #es_admin = session.get("is_admin")
    es_admin = is_admin()

    rows_con_puntos = []

    for r in rows:
        local, visitante, gl, gv, pl, pv, fecha_hora = r

        # 🔥 FIX: convertir a datetime
        fecha_dt = None
        if fecha_hora:
            try:
                fecha_dt = datetime.fromisoformat(fecha_hora).replace(tzinfo=None)
            except:
                fecha_dt = None  # por si viene raro

        # ocultar si no empezó
        oculto = False
        if fecha_dt and fecha_dt > now and username != usuario_logueado and not es_admin:
            pl, pv = None, None
            oculto = True

        pts = calcular_puntos(gl, gv, pl, pv) if gl is not None else None




        rows_con_puntos.append(
            (local, visitante, gl, gv, pl, pv, pts, oculto)
        )

    conn.close()


    total_puntos = sum(
        p for *_, p, _ in rows_con_puntos
        if p is not None
    )

    # 🔥 fix temporal partido eliminado
    if fecha_sel == 1:
        fixes = {
            "mariano": 3,
            "rafael": 1,
            "seba silva": 1
        }

        total_puntos += fixes.get(username, 0)

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
@limiter.limit("10 per minute")
def index():
    if request.method == "POST":
        username = request.form["username"].strip().lower()
        password = request.form["password"]


        import re

        if not re.match(r"^[a-z0-9_]{3,20}$", username):
            flash("Usuario o contraseña incorrectos", "error")
            return redirect("/")

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM users WHERE username=?", (username,))
        user = cursor.fetchone()

        conn.close()

        if user and check_password_hash(user[2], password):
            session["user"] = username
            session["is_admin"] = False

            if request.args.get("app") == "1":
                token = generar_token(username)
                return redirect(f"/autologin/{token}")

            return redirect("/matches")

        flash("Usuario o contraseña incorrectos", "error")
        return redirect("/")

    return render_template("index.html")

from flask import jsonify
from flask_limiter.errors import RateLimitExceeded

@app.errorhandler(RateLimitExceeded)
def ratelimit_handler(e):
    return "Demasiados intentos. Esperá un minuto.", 429


# =========================
# ADMIN LOGIN
# =========================
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():

    if is_admin():
        return redirect("/admin")

    if request.method == "POST":

        password = request.form.get("password")

        if password == ADMIN_PASSWORD and session.get("user") in ADMINS:
            session["is_admin"] = True
            return redirect("/admin")

        flash("Clave admin incorrecta", "error")

    return render_template("admin_login.html")


# =========================
# REGISTER
# =========================
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip().lower()
        import re

        if not re.match(r"^[a-z0-9_]{3,20}$", username):
            flash("Usuario inválido", "error")
            return redirect("/register")
        password = request.form["password"]
        if len(password) < 8:
            flash("La contraseña debe tener al menos 8 caracteres", "error")
            return redirect("/register")


        telefono = request.form.get("telefono")
        email = request.form.get("email")
        if "@" not in email:
            flash("Email inválido", "error")
            return redirect("/register")

        if not email:
            flash("Email requerido", "error")
            return redirect("/register")

        email = email.strip().lower()

        if not telefono or len(telefono) < 7:
            flash("Teléfono inválido", "error")
            return redirect("/register")

        avatar_tipo = request.form.get("avatar_tipo")

        hashed_password = generate_password_hash(password)
        avatar = "/static/img/default_m.png"

        avatar_map = {
            "m": "/static/img/default_m.png",
            "f": "/static/img/default_f.png",
            "f2": "/static/img/default_f_dark.png",
            "m_old": "/static/img/default_m_old.png",
            "f_old": "/static/img/default_f_old.png",
            "nb": "/static/img/default_nb.png"
        }
        avatar = avatar_map.get(avatar_tipo, "/static/img/default_m.png")


        file = request.files.get("avatar_file")

        # 🔒 límite de tamaño (2MB)
        #MAX_SIZE = 2 * 1024 * 1024  # 2MB
        MAX_SIZE = 5 * 1024 * 1024  # 5MB
        if file and file.filename != "":
            file.seek(0, os.SEEK_END)
            file_length = file.tell()
            file.seek(0)

            if file_length > MAX_SIZE:
                flash("La imagen es demasiado grande (máx 2MB)", "error")
                return redirect("/register")

            filename = f"{username}.jpg"
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)

            # abrir imagen
            try:
                img = Image.open(file)
            except:
                flash("Archivo de imagen inválido", "error")
                return redirect("/register")

            # convertir a RGB
            img = img.convert("RGB")

            # 🔥 recorte cuadrado (centrado)
            width, height = img.size
            min_side = min(width, height)

            left = (width - min_side) // 2
            top = (height - min_side) // 2
            right = (width + min_side) // 2
            bottom = (height + min_side) // 2

            img = img.crop((left, top, right, bottom))

            # 🔥 resize final (ej: 200x200)
            img = img.resize((200, 200), Image.LANCZOS)

            # 🔥 guardar optimizado
            img.save(filepath, format="JPEG", quality=75, optimize=True)

            avatar = f"/avatars/{filename}"

        conn = get_db()
        cursor = conn.cursor()

        telefono = telefono.strip()

        # 🔒 teléfono único
        cursor.execute("SELECT 1 FROM users WHERE telefono = ?", (telefono,))
        if cursor.fetchone():
            conn.close()
            flash("Ese teléfono ya está registrado", "error")
            return redirect("/register")

        try:
            cursor.execute(
                "INSERT INTO users (username, password, avatar, telefono, email) VALUES (?, ?, ?, ?, ?)",
                (username, hashed_password, avatar, telefono, email)
            )

            cursor.execute(
                "INSERT INTO scores (user, puntos) VALUES (?, 0)",
                (username,)
            )

            conn.commit()

        except sqlite3.IntegrityError:
            conn.close()
            flash("El usuario ya existe", "error")
            return redirect("/register")

        conn.close()
        session["user"] = username
        session["is_admin"] = False
        return redirect("/matches")

    return render_template("register.html")



@app.route("/autologin/<token>")
def autologin(token):
    # validar token (simple por ahora)
    username = validar_token(token)

    if not username:
        return "invalid"

    session["user"] = username
    session["is_admin"] = False

    return redirect("/matches")


def generar_token(username):
    return f"{username}:{int(time.time())}"

def validar_token(token):
    try:
        username, ts = token.split(":")
        if int(time.time()) - int(ts) > 60:
            return None
        return username
    except:
        return None



@app.route("/admin/reset_torneo", methods=["POST"])
def reset_torneo():
    if not is_admin():
        return redirect("/admin/login")

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM prediction")
    cursor.execute("DELETE FROM matches")
    cursor.execute("DELETE FROM payments")
    cursor.execute("UPDATE scores SET puntos = 0")
    cursor.execute("DELETE FROM fecha_history")

    recalcular_ranking(conn)

    conn.commit()
    conn.close()

    flash("Torneo reseteado completamente", "success")
    return redirect("/admin")



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

    # 🔥 por defecto mostrar la última fecha cargada
    cursor.execute("""
        SELECT MAX(fecha_num)
        FROM matches
    """)

    fecha_actual = cursor.fetchone()[0]

    # 🔥 si viene una fecha específica por URL
    if fecha_sel:
        fecha_actual = fecha_sel

    # 🔥 por seguridad
    if fecha_actual is None:
        cursor.execute("""
            SELECT MIN(fecha_num)
            FROM matches
        """)
        fecha_actual = cursor.fetchone()[0]

    # 🔥 límites navegación
    cursor.execute("""
        SELECT MIN(fecha_num), MAX(fecha_num)
        FROM matches
    """)

    min_fecha, max_fecha = cursor.fetchone()

    # 🔥 partidos
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

    now = now_uy().isoformat()

    pago_ok = pago_habilitado(user, fecha_actual)

    payments_enabled = PAYMENTS_ENABLED

    is_admin_user = user in ADMINS

    fecha_max_permitida = max_fecha

    return render_template(
        "matches.html",
        matches=matches_data,
        user=user,
        logos=logos,
        fecha_actual=fecha_actual,
        min_fecha=min_fecha,
        max_fecha=max_fecha,
        fecha_max_permitida=fecha_max_permitida,
        now=now,
        pago_ok=pago_ok,
        payments_enabled=payments_enabled,
        is_admin_user=is_admin_user,
        price=PRICE_PER_FECHA
    )
# -------------------------------------
@app.route("/admin/reset_scores", methods=["POST"])
def reset_scores():
    if not is_admin():
        return redirect("/admin/login")

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("UPDATE scores SET puntos = 0")

    conn.commit()
    conn.close()

    flash("Puntajes reseteados", "success")
    return redirect("/admin")
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
    cursor.execute("""
                   SELECT fecha_hora, fecha_num
                   FROM matches
                   WHERE id = ?
                   """, (match_id,))

    row = cursor.fetchone()

    if row:
        fecha_partido = datetime.fromisoformat(row[0])

        if fecha_partido.tzinfo:
            fecha_partido = fecha_partido.replace(tzinfo=None)

        fecha_num = row[1]

        # 🔥 BLOQUEO POR PAGO
        if not puede_jugar(user, fecha_num):
            conn.close()
            flash("Para guardar tu predicción tenés que pagar esta fecha", "error")
            return redirect(f"/crear_pago/{fecha_num}")

        # 🔒 BLOQUEO POR TIEMPO
        if now_uy() >= fecha_partido:
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

@app.route("/admin/nuevo_torneo", methods=["POST"])
def nuevo_torneo():
    if not is_admin():
        return redirect("/admin/login")

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT MAX(fecha_num) FROM matches")
    max_fecha = cursor.fetchone()[0] or 0

    session["base_fecha"] = max_fecha

    conn.close()

    flash(f"Nuevo torneo iniciado (desde fecha {max_fecha + 1})", "success")
    return redirect("/admin")


@app.route("/admin/payments")
def admin_payments():
    if not is_admin():
        return redirect("/admin/login")

    conn = get_db()
    cursor = conn.cursor()

    fecha = request.args.get("fecha")

    # fechas válidas del torneo actual
    cursor.execute("""
        SELECT DISTINCT fecha_num
        FROM matches
        ORDER BY fecha_num DESC
    """)

    fechas = [f[0] for f in cursor.fetchall()]

    # si no eligió nada, usar la última fecha
    if not fecha and fechas:
        fecha = str(fechas[0])

    # recién ahora consultar pagos
    if fecha:
        cursor.execute("""
            SELECT p.user, p.fecha_num, p.status, u.telefono, u.email
            FROM payments p
            LEFT JOIN users u ON p.user = u.username
            WHERE p.fecha_num = ?
            ORDER BY p.user
        """, (fecha,))
    else:
        cursor.execute("""
            SELECT p.user, p.fecha_num, p.status, u.telefono, u.email
            FROM payments p
            LEFT JOIN users u ON p.user = u.username
            ORDER BY p.user
        """)

    pagos = cursor.fetchall()

    conn.close()

    return render_template(
        "admin_payments.html",
        pagos=pagos,
        fechas=fechas,
        fecha_actual=fecha
    )


@app.route("/debug_matches")
def debug_matches():
    if not is_admin():
        return redirect("/admin/login")

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id,
            local,
            visitante,
            fecha_num,
            fecha_hora,
            goles_local,
            goles_visitante
        FROM matches
        ORDER BY fecha_num, fecha_hora
    """)

    rows = cursor.fetchall()

    conn.close()

    html = ""

    for r in rows:
        html += f"""
        <div style='margin-bottom:12px'>
            id={r[0]} |
            {r[1]} vs {r[2]} |
            fecha={r[3]} |
            hora={r[4]} |
            goles={r[5]}-{r[6]}
        </div>
        """

    return html


@app.route("/admin/create_history_table")
def create_history_table():

    if not is_admin():
        return redirect("/")

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fecha_history (
            fecha_num INTEGER,
            user TEXT,
            puntos INTEGER,
            posicion INTEGER,
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()

    return "OK"

@app.route("/history")
def history():

    if "user" not in session:
        return redirect("/")

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT DISTINCT fecha_num
        FROM matches
        ORDER BY fecha_num DESC
    """)

    fechas = [f[0] for f in cursor.fetchall()]

    history = []

    for fecha in fechas:

        cursor.execute("""
            SELECT p.user,
                   SUM(
                       CASE
                           WHEN m.goles_local = p.pred_local
                            AND m.goles_visitante = p.pred_visitante
                               THEN 3

                           WHEN (
                               (m.goles_local > m.goles_visitante AND p.pred_local > p.pred_visitante)
                            OR (m.goles_local < m.goles_visitante AND p.pred_local < p.pred_visitante)
                            OR (m.goles_local = m.goles_visitante AND p.pred_local = p.pred_visitante)
                           )
                               THEN 1

                           ELSE 0
                       END
                   ) pts
            FROM prediction p
            JOIN matches m ON p.match_id = m.id
            WHERE m.fecha_num = ?
              AND m.goles_local IS NOT NULL
              AND m.goles_visitante IS NOT NULL
            GROUP BY p.user
        """, (fecha,))

        tabla = {
            u: pts for u, pts in cursor.fetchall()
        }

        # 🔥 fix fecha 1
        if fecha == 1:

            fix_manual_fecha = {
                "mariano": 3,
                "rafael": 1,
                "seba silva": 1
            }

            for u, pts in fix_manual_fecha.items():
                tabla[u] = tabla.get(u, 0) + pts

        tabla = sorted(
            tabla.items(),
            key=lambda x: (-x[1], x[0])
        )

        tabla_pos = []

        last_pts = None
        current_pos = 0

        for user, pts in tabla:

            if pts != last_pts:
                current_pos += 1

            tabla_pos.append(
                (current_pos, user, pts)
            )

            last_pts = pts

        history.append({
            "fecha": fecha,
            "tabla": tabla_pos
        })

    conn.close()

    return render_template(
        "history.html",
        history=history
    )

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
    now = now_uy()

    # 🔹 saber si la fecha global terminó (lo que ya tenías)

    # 🔹 fecha seleccionada
    fecha_sel = request.args.get("fecha", type=int)

    if not fecha_sel:

        cursor.execute("""
            SELECT fecha_num
            FROM matches
            WHERE datetime(fecha_hora) <= datetime(?)
            ORDER BY fecha_num DESC
            LIMIT 1
        """, (now_uy().isoformat(),))

        row = cursor.fetchone()

        if row:
            fecha_sel = row[0]
        else:
            cursor.execute("""
                SELECT MIN(fecha_num)
                FROM matches
            """)
            fecha_sel = cursor.fetchone()[0]

    # 🔹 saber si la fecha seleccionada terminó

    cursor.execute("""
        SELECT COUNT(*)
        FROM matches
        WHERE fecha_num = ?
          AND (
              goles_local IS NULL
              OR goles_visitante IS NULL
          )
    """, (fecha_sel,))

    pendientes_fecha = cursor.fetchone()[0]

    fecha_jugada = pendientes_fecha == 0
    cursor.execute("""
        SELECT COUNT(*)
        FROM matches
        WHERE fecha_num = ?
          AND (
              goles_local IS NULL
              OR goles_visitante IS NULL
          )
    """, (fecha_sel,))

    pendientes = cursor.fetchone()[0]

    if pendientes == 0:
        fecha_estado = "Finalizada"
    else:
        fecha_estado = f"En juego · {pendientes} pendiente(s)"

    # 🔹 puntos por fecha
    pts_fecha = {}   # 🔥 IMPORTANTE (evita crash)

    cursor.execute("""
SELECT p.user,
       SUM(
           CASE
               WHEN m.goles_local IS NOT NULL AND m.goles_visitante IS NOT NULL THEN
                   CASE
                       WHEN m.goles_local = p.pred_local 
                            AND m.goles_visitante = p.pred_visitante THEN 3
                       WHEN (
                            (m.goles_local > m.goles_visitante AND p.pred_local > p.pred_visitante)
                         OR (m.goles_local < m.goles_visitante AND p.pred_local < p.pred_visitante)
                         OR (m.goles_local = m.goles_visitante AND p.pred_local = p.pred_visitante)
                       ) THEN 1
                       ELSE 0
                   END
               ELSE 0
           END
       ) as pts
FROM prediction p
JOIN matches m ON p.match_id = m.id
WHERE m.fecha_num = ?
  AND m.goles_local IS NOT NULL
  AND m.goles_visitante IS NOT NULL
GROUP BY p.user
                   """, (fecha_sel,))

    pts_fecha = {u: pts for u, pts in cursor.fetchall()}

    # 🔥 fix temporal partido eliminado
    """
    fix_manual_fecha = {
        "mariano": 3,
        "rafael": 1,
        "seba silva": 1
    }
    """

    for u, pts in fix_manual_fecha.items():
        pts_fecha[u] = pts_fecha.get(u, 0) + pts
    # -----------------------------------
    """
    tabla_fecha = sorted(pts_fecha.items(), key=lambda x: -x[1])

    top3_fecha = tabla_fecha[:3]
    resto_fecha = tabla_fecha[3:]
    """

    tabla_fecha = sorted(
        pts_fecha.items(),
        key=lambda x: (-x[1], x[0])
    )

    tabla_fecha_pos = []

    last_pts = None
    current_pos = 0

    for user, pts in tabla_fecha:

        if pts != last_pts:
            current_pos += 1

        tabla_fecha_pos.append(
            (current_pos, user, pts)
        )

        last_pts = pts

    top3_fecha = tabla_fecha_pos[:3]
    resto_fecha = tabla_fecha_pos[3:]


    ganador_fecha = None

    if pts_fecha:
        max_puntos = max(pts_fecha.values())

        if max_puntos > 0:
            ganador_fecha = [
                u for u, pts in pts_fecha.items() if pts == max_puntos
            ]

    conn.close()  # 🔥 ahora sí, al final

    ranking = sorted(ranking, key=lambda x: (-x[2], x[0]))

    ranking_with_pos = []

    last_pts = None
    current_pos = 0

    for username, avatar, pts in ranking:

        if pts != last_pts:
            current_pos += 1

        ranking_with_pos.append(
            (current_pos, username, avatar, pts)
        )

        last_pts = pts

    ranking = ranking_with_pos


    return render_template(
        "ranking.html",
        ranking=ranking,
        current_user=session.get("user"),
        user=session.get("user"),
        fecha_sel=fecha_sel,
        pts_fecha=pts_fecha,
        ganador_fecha=ganador_fecha,
        fecha_estado=fecha_estado,
        fecha_jugada=fecha_jugada,
        tabla_fecha=tabla_fecha,
        top3_fecha=top3_fecha,
        resto_fecha=resto_fecha,
        is_admin=is_admin()
    )



from datetime import datetime, timedelta


@app.route("/fix_scores")
def fix_scores():
    if not is_admin():
        return redirect("/admin/login")
    conn = get_db()
    cursor = conn.cursor()

    fixes = {
        "mariano": 3,
        "rafael": 1,
        "seba silva": 1
    }

    for u, pts in fixes.items():

        cursor.execute("""
            UPDATE scores
            SET puntos = puntos + ?
            WHERE user = ?
        """, (pts, u))

    conn.commit()
    conn.close()

    return "OK"
def now_uy():
    return datetime.utcnow() - timedelta(hours=3)
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


    fecha_sel = request.args.get("fecha", type=int)

    if not fecha_sel:
        cursor.execute("SELECT MAX(fecha_num) FROM matches")
        fecha_sel = cursor.fetchone()[0]

    conn.close()
    return render_template(
        "admin.html",
        user=session.get("user"),
        users=users,
        fecha_sel=fecha_sel  # 👈
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
        base = session.get("base_fecha", 0)
        fecha_num = base + int(request.form["fecha_num"])

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

@app.route("/admin/edit_match_date/<int:match_id>", methods=["POST"])
def edit_match_date(match_id):

    if not is_admin():
        return redirect("/admin/login")

    nueva_fecha = request.form["fecha"]
    nueva_hora = request.form["hora"]

    nueva_dt = datetime.fromisoformat(f"{nueva_fecha}T{nueva_hora}")

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE matches
        SET fecha_hora = ?
        WHERE id = ?
    """, (nueva_dt.isoformat(), match_id))

    conn.commit()

    # volver a la fecha correcta
    cursor.execute("""
        SELECT fecha_num
        FROM matches
        WHERE id = ?
    """, (match_id,))

    fecha_num = cursor.fetchone()[0]

    conn.close()

    return redirect(f"/admin/matches?fecha_num={fecha_num}")
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
        cursor.execute(
            "SELECT fecha_num FROM matches WHERE id=?",
            (match_id,)
        )

        fecha_sel = cursor.fetchone()[0]
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



        #recalcular_ranking()
        recalcular_ranking(conn)

        # 🔥 fix temporal partido eliminado
        fixes = {
            "mariano": 3,
            "rafael": 1,
            "seba silva": 1
        }

        cursor = conn.cursor()

        for u, pts in fixes.items():
            cursor.execute("""
                UPDATE scores
                SET puntos = puntos + ?
                WHERE user = ?
            """, (pts, u))

        conn.commit()


        conn.commit()

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



    # 🔥 CLAVE
    recalcular_ranking(conn)
    conn.commit()
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

@app.route("/reset/<token>", methods=["GET", "POST"])
def reset(token):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT email, created_at FROM reset_tokens WHERE token=?",
        (token,)
    )
    row = cursor.fetchone()

    if not row:
        conn.close()
        return "Token inválido"

    email, created_at = row

    # 🔥 expiración 1 hora
    if int(time.time()) - created_at > 3600:
        cursor.execute("DELETE FROM reset_tokens WHERE token=?", (token,))
        conn.commit()
        conn.close()
        return "Token expirado"

    if request.method == "POST":
        password = request.form["password"]

        if len(password) < 8:
            conn.close()
            flash("Mínimo 8 caracteres", "error")
            return redirect(request.url)

        hashed = generate_password_hash(password)

        cursor.execute(
            "UPDATE users SET password=? WHERE email=?",
            (hashed, email)
        )

        # 🔥 borrar token usado
        cursor.execute("DELETE FROM reset_tokens WHERE token=?", (token,))

        conn.commit()
        conn.close()

        flash("Contraseña actualizada", "success")
        return redirect("/")

    conn.close()
    return render_template("reset.html")



@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")
import smtplib
from email.mime.text import MIMEText
@app.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form["email"].strip().lower()

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("SELECT 1 FROM users WHERE email=?", (email,))
        if not cursor.fetchone():
            conn.close()
            flash("Te enviamos las instrucciones al mail", "success")
            return redirect("/forgot_password")

        # 🔥 generar token
        token = os.urandom(24).hex()
        now = int(time.time())

        # 🔥 limpiar tokens viejos (1 hora)
        cursor.execute("DELETE FROM reset_tokens WHERE created_at < ?", (now - 3600,))

        # 🔥 guardar token
        cursor.execute(
            "INSERT INTO reset_tokens (email, token, created_at) VALUES (?, ?, ?)",
            (email, token, now)
        )

        conn.commit()
        conn.close()

        link = f"https://penca-tuerta.onrender.com/reset/{token}"

        enviar_mail(
            email,
            "Recuperar contraseña",
            f"Entrá acá para cambiar tu contraseña:\n\n{link}\n\nExpira en 1 hora."
        )

        flash("Te enviamos un mail para recuperar la contraseña", "success")
        return redirect("/")

    return render_template("forgot_password.html")

def enviar_mail(destino, asunto, cuerpo):
    remitente = os.getenv("MAIL_USER")
    password = os.getenv("MAIL_PASS")

    msg = MIMEText(cuerpo)
    msg["Subject"] = asunto
    msg["From"] = remitente
    msg["To"] = destino

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(remitente, password)
        server.send_message(msg)

"""
@app.route("/fix_db")
def fix_db():
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("ALTER TABLE users ADD COLUMN email TEXT")
        conn.commit()
        return "OK"
    except Exception as e:
        return str(e)
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)