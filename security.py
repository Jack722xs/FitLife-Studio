"""Autenticación, sesiones y administración de cuentas internas."""
import hashlib
import re
import secrets
import time

from storage import ROLES, Rule, audit, choice, connect, get, hash_password, integer, require, text, user_password, valid_password

DUMMY_HASH = hash_password(secrets.token_urlsafe(24))


def username(data):
    name = text(data, "usuario", 3, 40).lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,39}", name):
        raise Rule("El usuario admite letras sin acentos, números, puntos, guiones y guiones bajos.")
    return name


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def login(data):
    name = username(data)
    password = data.get("clave")
    if not isinstance(password, str) or not 1 <= len(password) <= 128:
        raise Rule("Credenciales inválidas.", 401)
    with connect() as conn:
        account = get(conn, "SELECT * FROM users WHERE name=?", (name,))
    valid = valid_password(password, account["password"] if account else DUMMY_HASH)
    current = int(time.time())
    with connect(write=True) as conn:
        latest = get(conn, "SELECT * FROM users WHERE name=?", (name,))
        if not latest or not latest["active"] or latest["locked"] > current:
            audit(conn, latest["id"] if latest else None, "inicio denegado", "sesión")
            return None
        if not valid or not account or latest["password"] != account["password"]:
            failed = latest["failed"] + 1
            locked = current + 900 if failed >= 5 else 0
            conn.execute("UPDATE users SET failed=?,locked=? WHERE id=?", (0 if locked else failed, locked, latest["id"]))
            audit(conn, latest["id"], "bloqueo temporal" if locked else "inicio denegado", "sesión")
            return None
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        conn.execute("UPDATE users SET failed=0,locked=0 WHERE id=?", (latest["id"],))
        conn.execute("DELETE FROM sessions WHERE expires<? OR touched<?", (current, current - 1800))
        conn.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", (token_hash(token), latest["id"], csrf, current, current + 28800))
        audit(conn, latest["id"], "inicio correcto", "sesión")
        return token


def session(conn, token):
    current = int(time.time())
    if not token or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise Rule("Inicia sesión para continuar.", 401)
    result = get(conn, """SELECT u.id,u.name,u.role,s.csrf,s.token,s.touched,s.expires FROM sessions s
                   JOIN users u ON u.id=s.user_id WHERE u.active=1 AND s.token=? AND s.touched>?
                   AND s.expires>?""", (token_hash(token), current - 1800, current))
    if not result:
        raise Rule("Tu sesión terminó. Inicia sesión para continuar.", 401)
    conn.execute("UPDATE sessions SET touched=? WHERE token=?", (current, result["token"]))
    return result


def user_form(conn, actor, data):
    user_id = integer(data, "id", 0)
    role = choice(data, "rol", ROLES)
    active = integer(data, "activo", 0, 1)
    if user_id:
        previous = require(conn, "SELECT * FROM users WHERE id=?", (user_id,))
        if user_id == actor["id"] and (role != "admin" or not active):
            raise Rule("Tu cuenta de Administración debe conservar su rol y estado.")
        encoded = hash_password(user_password(data)) if data.get("clave") else previous["password"]
        conn.execute("UPDATE users SET role=?,active=?,password=?,failed=0,locked=0 WHERE id=?",
                     (role, active, encoded, user_id))
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    else:
        user_id = conn.execute("INSERT INTO users(name,password,role,active) VALUES(?,?,?,?)",
                               (username(data), hash_password(user_password(data)), role, active)).lastrowid
    audit(conn, actor["id"], "guardar permisos", "usuario", user_id, f"rol {role}, activo {active}")
    return {"message": "Cuenta guardada. Los cambios requieren iniciar sesión de nuevo."}
