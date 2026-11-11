"""Persistencia, validación común y credenciales del prototipo local."""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get("FITLIFE_DB", ROOT / "datos" / "fitlife.sqlite3")).resolve()
METHODS = ("efectivo", "tarjeta", "transferencia")
ROLES = ("admin", "recepcion", "entrenador")
PLANS = [
    ("Mensual Libre", "mensual", 30, 29990, 0),
    ("Trimestral", "periodico", 90, 79990, 15),
    ("Semestral", "periodico", 180, 149990, 20),
    ("Anual", "periodico", 365, 259990, 30),
    ("Pase Diario", "diario", 1, 5990, 0),
]


class Rule(Exception):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def now():
    return datetime.now().astimezone()


def stamp(value=None):
    return (value or now()).replace(tzinfo=None).isoformat(timespec="seconds")


def text(data, key, minimum=1, maximum=120):
    value = data.get(key, "")
    if not isinstance(value, str):
        raise Rule(f"Campo inválido: {key}.")
    value = value.strip()
    if not minimum <= len(value) <= maximum or any(ord(c) < 32 for c in value):
        raise Rule(f"Revisa la longitud y el contenido de {key}.")
    return value


def integer(data, key, minimum=1, maximum=99_999_999):
    value = data.get(key, "")
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]{1,10}", str(value)):
        raise Rule(f"Ingresa un número válido en {key}.")
    value = int(value)
    if not minimum <= value <= maximum:
        raise Rule(f"Valor fuera de rango en {key}.")
    return value


def choice(data, key, options):
    value = text(data, key)
    if value not in options:
        raise Rule(f"Selecciona una opción válida en {key}.")
    return value


def day(data, key):
    value = text(data, key, 10, 10)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise Rule(f"Fecha inválida en {key}.") from None


def rut(value):
    compact = value.upper().replace(".", "").replace("-", "").replace(" ", "")
    if not re.fullmatch(r"[0-9]{1,8}[0-9K]", compact):
        raise Rule("RUT inválido. Incluye el dígito verificador.")
    base = str(int(compact[:-1]))
    check = 11 - sum(int(x) * (i % 6 + 2) for i, x in enumerate(base[::-1])) % 11
    if base == "0" or compact[-1] != {11: "0", 10: "K"}.get(check, str(check)):
        raise Rule("El dígito verificador del RUT no coincide.")
    return f"{base}-{compact[-1]}"


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=3,
                            maxmem=64 * 1024 * 1024)
    return salt + "$" + digest.hex()


def valid_password(password, encoded):
    return hmac.compare_digest(hash_password(password, encoded.split("$")[0]), encoded)


def user_password(data):
    password = data.get("clave", "")
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        raise Rule("Usa una contraseña de 12 a 128 caracteres.")
    return password


@contextmanager
def connect(write=False):
    conn = sqlite3.connect(DB, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        if write:
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def get(conn, sql, args=()):
    record = conn.execute(sql, args).fetchone()
    return dict(record) if record is not None else None


def require(conn, sql, args=()):
    record = get(conn, sql, args)
    if record is None:
        raise Rule("Registro no encontrado.", 404)
    return record


def rows(conn, sql, args=()):
    return [dict(record) for record in conn.execute(sql, args)]


def audit(conn, user, event, entity, target=None, detail="", origin="local"):
    conn.execute("INSERT INTO audit(user_id,at,event,entity,target,detail,origin) VALUES(?,?,?,?,?,?,?)",
                 (user, stamp(), event, entity, target, detail, origin))


def init():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript((ROOT / "schema.sql").read_text(encoding="utf-8-sig"))
        for table in ("payments", "settlements", "notes", "adjustments", "access", "closures", "audit", "commands"):
            for action in ("UPDATE", "DELETE"):
                conn.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{action}
                    BEFORE {action} ON {table}
                    BEGIN SELECT RAISE(ABORT,'Registro histórico inmutable'); END""")
    with connect(write=True) as conn:
        conn.execute("INSERT OR IGNORE INTO settings VALUES('lease_key',?)", (secrets.token_hex(32),))
        if not get(conn, "SELECT 1 FROM plans LIMIT 1"):
            conn.executemany("INSERT INTO plans(name,kind,days,price,freeze_days) VALUES(?,?,?,?,?)", PLANS)

