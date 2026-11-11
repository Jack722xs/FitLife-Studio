"""Punto de entrada. HTTP de bucle local para la demostración académica."""
import argparse
import getpass
import hmac
import json
import logging
import re
import sqlite3
import uuid
from datetime import timedelta
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from finance import adjust, cash_view, close_cash, confirm, member_payments, payment_note, reports, sell
from operations import access_record, command, deal_form, freeze_decide, freeze_request, member_form, memberships, offline_snapshot, plan_form
from security import login, session, user_form, username
from storage import DB, ROOT, Rule, audit, connect, get, hash_password, init, integer, now, require, rows, rut, stamp, text, user_password

OPERATORS = ("admin", "recepcion")
ADMIN = ("admin",)
ALL = ("admin", "recepcion", "entrenador")
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
}


def profile(conn, user, data):
    member_id = integer(data, "id")
    record = require(conn, "SELECT * FROM members WHERE id=?", (member_id,))
    return {"member": record, "memberships": memberships(conn, member_id, str(now().date())),
            "payments": member_payments(conn, member_id)}


def search(conn, user, data):
    term = text(data, "q", 0, 100)
    try:
        identifier = rut(term)
    except Rule:
        identifier = ""
    if user["role"] == "entrenador":
        return rows(conn, """SELECT name,CASE WHEN born IS NOT NULL THEN
            CAST(strftime('%Y.%m%d','now','localtime')-strftime('%Y.%m%d',born) AS INTEGER) END AS age,
            emergency,emergency_phone FROM members WHERE instr(lower(name),lower(?))>0
            ORDER BY name LIMIT 80""", (term,))
    return rows(conn, """SELECT id,name,rut FROM members WHERE rut=? OR instr(lower(name),lower(?))>0
                         ORDER BY name LIMIT 80""", (identifier, term))


def catalog(conn, user, data):
    admin = user["role"] == "admin"
    return {"plans": rows(conn, "SELECT * FROM plans WHERE active=1 OR ? ORDER BY id", (admin,)),
            "deals": rows(conn, "SELECT * FROM deals WHERE ? OR (active=1 AND ? BETWEEN starts AND ends) ORDER BY id",
                          (admin, str(now().date())))}


def freeze_list(conn, user, data):
    return rows(conn, """SELECT f.*,s.name,m.plan_name FROM freezes f JOIN memberships m ON m.id=f.membership_id
              JOIN members s ON s.id=m.member_id ORDER BY (decision='pendiente') DESC,f.id DESC LIMIT 80""")


def activity(conn, user, data):
    return rows(conn, """SELECT a.id,a.at,a.allowed,a.reason,a.source,m.name FROM access a
                         LEFT JOIN members m ON m.id=a.member_id
                         WHERE ? OR a.user_id=? ORDER BY a.id DESC LIMIT 25""",
                (user["role"] == "admin", user["id"]))


def audit_list(conn, user, data):
    return rows(conn, """SELECT a.*,u.name AS operator FROM audit a LEFT JOIN users u ON u.id=a.user_id
                          ORDER BY a.id DESC LIMIT 150""")


def account_list(conn, user, data):
    return rows(conn, "SELECT id,name,role,active FROM users ORDER BY name")


def cashiers(conn, user, data):
    return rows(conn, "SELECT id,name FROM users WHERE role IN ('admin','recepcion') OR id IN (SELECT user_id FROM ledger) ORDER BY name") if user["role"] == "admin" else [
        {"id": user["id"], "name": user["name"]}]


def receipt(conn, user, data):
    payment = require(conn, """SELECT p.*,s.at AS confirmed,m.plan_name,u.name AS operator,b.name
        FROM payments p JOIN settlements s ON s.payment_id=p.id
        JOIN memberships m ON m.id=p.membership_id JOIN users u ON u.id=s.user_id
        JOIN members b ON b.id=m.member_id WHERE p.id=?""", (integer(data, "id"),))
    return {"payment": payment,
            "adjustments": rows(conn, "SELECT amount,reason,at FROM adjustments WHERE payment_id=?", (payment["id"],)),
            "notes": rows(conn, "SELECT reason,at FROM notes WHERE payment_id=?", (payment["id"],))}


def logout(conn, user, data):
    conn.execute("DELETE FROM sessions WHERE token=?", (user["token"],))
    audit(conn, user["id"], "cierre", "sesión")
    return {"message": "Sesión cerrada."}


READ = {
    "/api/session": (ALL, lambda c, u, d: {**{k: u[k] for k in ("id", "name", "role", "csrf")},
                                          "day": str(now().date()), "shift": "AM" if now().hour < 12 else "PM"}),
    "/api/members": (ALL, search), "/api/member": (OPERATORS, profile), "/api/catalog": (OPERATORS, catalog),
    "/api/freezes": (OPERATORS, freeze_list), "/api/access": (OPERATORS, activity),
    "/api/snapshot": (OPERATORS, lambda c, u, d: offline_snapshot(c, u)),
    "/api/users": (ADMIN, account_list), "/api/audit": (ADMIN, audit_list),
    "/api/cash": (OPERATORS, cash_view), "/api/cashiers": (OPERATORS, cashiers),
    "/api/reports": (ADMIN, lambda c, u, d: reports(c)), "/api/receipt": (OPERATORS, receipt),
}
WRITE = {
    "/api/logout": (ALL, logout), "/api/member": (OPERATORS, member_form),
    "/api/plan": (ADMIN, plan_form), "/api/deal": (ADMIN, deal_form),
    "/api/access": (OPERATORS, lambda c, u, d: command(c, u, d, "acceso", access_record)),
    "/api/sale": (OPERATORS, lambda c, u, d: command(c, u, d, "venta", sell)),
    "/api/freeze": (OPERATORS, freeze_request), "/api/decide": (ADMIN, freeze_decide),
    "/api/confirm": (OPERATORS, confirm), "/api/note": (OPERATORS, payment_note),
    "/api/adjust": (ADMIN, adjust), "/api/cash": (OPERATORS, close_cash),
    "/api/user": (ADMIN, user_form),
}


class Handler(BaseHTTPRequestHandler):
    server_version = "FitLife"
    sys_version = ""

    def send_content(self, status, payload, content_type, cookie=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; "
                         "connect-src 'self'; img-src 'self'; manifest-src 'self'; worker-src 'self'; "
                         "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if cookie is not None:
            self.send_header("Set-Cookie", f"fitlife={cookie}; Path=/; HttpOnly; SameSite=Strict"
                             + ("; Max-Age=0" if not cookie else ""))
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, status, result, cookie=None):
        self.send_content(status, json.dumps(result, ensure_ascii=False).encode(), "application/json; charset=utf-8", cookie)

    def body(self):
        size = self.headers.get("Content-Length", "")
        if not size.isascii() or not size.isdigit() or not 1 <= int(size) <= 16000:
            raise Rule("Tamaño de solicitud inválido.", 413)
        if self.headers.get_content_type() != "application/json":
            raise Rule("Se requiere JSON.", 415)
        try:
            data = json.loads(self.rfile.read(int(size)))
        except (ValueError, UnicodeError, RecursionError):
            raise Rule("JSON inválido.", 400) from None
        if not isinstance(data, dict) or any(not isinstance(v, (str, bool, int)) for v in data.values()):
            raise Rule("Se requiere un objeto de campos simples.", 400)
        return data

    def do_GET(self):
        self.dispatch(False)

    def do_POST(self):
        self.dispatch(True)

    def dispatch(self, writing):
        try:
            self.connection.settimeout(10)
            host = self.headers.get("Host")
            if host != f"127.0.0.1:{self.server.server_port}":
                raise Rule("Usa la dirección local indicada al iniciar.", 403)
            path = urlsplit(self.path)
            if not writing and path.path in ASSETS:
                name, content_type = ASSETS[path.path]
                return self.send_content(200, (ROOT / "web" / name).read_bytes(), content_type)
            if writing and self.headers.get("Origin") != f"http://{host}":
                raise Rule("Origen de solicitud no autorizado.", 403)
            data = self.body() if writing else {k: v[-1] for k, v in parse_qs(
                path.query, keep_blank_values=True, max_num_fields=20).items()}
            if writing and path.path == "/api/login":
                token = login(data)
                if not token:
                    raise Rule("Credenciales inválidas o cuenta temporalmente bloqueada.", 401)
                return self.send_json(200, {"message": "Sesión iniciada."}, token)
            route = (WRITE if writing else READ).get(path.path)
            if not route:
                raise Rule("Ruta no encontrada.", 404)
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            with connect(write=True) as conn:
                user = session(conn, cookie["fitlife"].value if "fitlife" in cookie else "")
                roles, action = route
                denied = user["role"] not in roles or (writing and not hmac.compare_digest(
                    self.headers.get("X-CSRF", "").encode(), user["csrf"].encode()))
                if denied:
                    audit(conn, user["id"], "solicitud denegada", "permiso", detail=path.path)
                    result = {"error": "Tu sesión no tiene permiso para esta operación."}
                else:
                    result = action(conn, user, data)
            self.send_json(403 if denied else 200, result, "" if path.path == "/api/logout" and not denied else None)
        except Rule as error:
            self.send_json(error.status, {"error": str(error)})
        except sqlite3.IntegrityError:
            self.send_json(409, {"error": "Hay un duplicado o una relación inválida. Revisa RUT, nombre o referencia."})
        except (CookieError, ValueError):
            self.send_json(400, {"error": "Solicitud inválida."})
        except (ConnectionError, TimeoutError):
            return
        except Exception:
            incident = uuid.uuid4().hex[:10]
            logging.exception("Incidente %s", incident)
            try:
                self.send_json(500, {"error": f"No se completó la operación. Incidente {incident}."})
            except OSError:
                pass

    def log_request(self, code="-", size="-"):
        # Las URL de búsqueda contienen datos personales. Registrar solo método y estado.
        if str(code).startswith("5"):
            logging.warning("%s %s", self.command, code)


def create_admin():
    name = username({"usuario": input("Usuario de Administración: ")})
    password = user_password({"clave": getpass.getpass("Contraseña (12 caracteres o más): ")})
    if password != getpass.getpass("Repite la contraseña: "):
        raise Rule("Las contraseñas no coinciden.")
    with connect(write=True) as conn:
        if get(conn, "SELECT 1 FROM users WHERE role='admin' AND active=1"):
            raise Rule("Ya existe un administrador activo. Gestiona otras cuentas desde la interfaz.")
        conn.execute("INSERT INTO users(name,password,role) VALUES(?,?,'admin')", (name, hash_password(password)))
    print("Administrador local creado.")


def demo():
    with connect(write=True) as conn:
        admin = require(conn, "SELECT id,role FROM users WHERE role='admin' AND active=1 ORDER BY id LIMIT 1")
        if get(conn, "SELECT 1 FROM members LIMIT 1"):
            raise Rule("La carga demostrativa requiere una base sin socios.")
        people = [("10000001-6", "Socia Demo Vigente"), ("10000002-4", "Socio Demo Vencido"),
                  ("10000003-2", "Socia Demo Transferencia"), ("10000004-0", "Socio Demo Anual")]
        for index, (identifier, name) in enumerate(people, 1):
            member_form(conn, admin, {"id": 0, "rut": identifier, "nombre": name, "telefono": "000000000",
                                     "correo": f"demo{index}@example.invalid", "emergencia": "Contacto Demo",
                                     "tel_emergencia": "000000001", "aviso": "si"})
        for index, (plan, price, method, reference) in enumerate([
            (1, 29990, "efectivo", ""), (1, 29990, "efectivo", ""),
            (1, 29990, "transferencia", "DEMO-TR-03"), (4, 259990, "tarjeta", "DEMO-POS-04")]):
            occurred = now() - timedelta(days=31) if index == 1 else None
            sell(conn, admin, {"rut": people[index][0], "plan": plan, "cotizacion": price,
                              "medio": method, "referencia": reference, "convenio": 0}, occurred)
    print("Cuatro socios y pagos de demostración cargados.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("serve", "init-admin", "seed-demo", "backup"), nargs="?", default="serve")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    init()
    try:
        if args.command == "init-admin":
            create_admin()
        elif args.command == "seed-demo":
            demo()
        elif args.command == "backup":
            if not args.output:
                raise Rule("Indica una ruta nueva mediante --output.")
            with args.output.open("xb"):
                pass
            with connect() as source, sqlite3.connect(args.output) as dest:
                source.backup(dest)
            print(f"Copia SQLite creada en {args.output}")
        else:
            with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
                print(f"FitLife Studio en http://127.0.0.1:{server.server_port}/", flush=True)
                server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
    except (Rule, OSError, sqlite3.Error) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()

