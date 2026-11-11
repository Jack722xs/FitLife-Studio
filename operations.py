"""Reglas de socios, planes, vigencia, acceso y contingencia."""
import hashlib
import hmac
import json
import re
from datetime import date, datetime, timedelta, timezone

from storage import Rule, audit, choice, day, get, integer, now, require, rows, rut, stamp, text

ONE_DAY = timedelta(days=1)


def member_form(conn, user, data):
    member_id = integer(data, "id", 0)
    name = text(data, "nombre", 2, 100)
    phone = text(data, "telefono", 6, 25)
    email = text(data, "correo", 5, 120).lower()
    emergency = text(data, "emergencia", 2, 100)
    emergency_phone = text(data, "tel_emergencia", 6, 25)
    address = text(data, "direccion", 0, 140)
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise Rule("Ingresa un correo válido.")
    if any(not re.fullmatch(r"[+0-9 ()-]{6,25}", p) or sum(c.isdigit() for c in p) < 6
           for p in (phone, emergency_phone)):
        raise Rule("Ingresa teléfonos válidos.")
    if member_id:
        old = require(conn, "SELECT * FROM members WHERE id=?", (member_id,))
        if user["role"] == "admin":
            born = day(data, "nacimiento") if data.get("nacimiento") else None
            if born and (born > now().date() or born.year < 1900):
                raise Rule("Fecha de nacimiento fuera de rango.")
            conn.execute("UPDATE members SET name=?,born=?,address=?,phone=?,email=?,emergency=?,emergency_phone=? WHERE id=?",
                         (name, born, address, phone, email, emergency, emergency_phone, member_id))
        else:
            conn.execute("UPDATE members SET address=?,phone=?,email=? WHERE id=?", (address, phone, email, member_id))
        audit(conn, user["id"], "editar", "socio", old["id"])
    else:
        identifier = rut(text(data, "rut"))
        born = day(data, "nacimiento") if data.get("nacimiento") else None
        if born and (born > now().date() or born.year < 1900):
            raise Rule("Fecha de nacimiento fuera de rango.")
        if data.get("aviso") != "si":
            raise Rule("Registra que se informó el propósito del tratamiento.")
        member_id = conn.execute(
            """INSERT INTO members(rut,name,born,address,phone,email,emergency,emergency_phone,notice_at)
               VALUES(?,?,?,?,?,?,?,?,?)""", (identifier, name, born, address, phone, email, emergency,
                                               emergency_phone, stamp())).lastrowid
        audit(conn, user["id"], "alta", "socio", member_id)
    return {"id": member_id, "message": "Ficha guardada."}


def membership_state(record, today):
    if record["void"]:
        return "Anulada"
    if not record["starts"]:
        return "Pendiente"
    if record["starts"] > today:
        return "Programada"
    if record["ends"] < today:
        return "Vencida"
    return "Congelada" if record["paused"] else "Activa"


def memberships(conn, member_id, today):
    records = rows(conn, """SELECT m.*, EXISTS(SELECT 1 FROM freezes f WHERE f.membership_id=m.id
               AND f.decision='aprobada' AND ? BETWEEN f.starts AND f.ends) AS paused
               FROM memberships m WHERE m.member_id=? ORDER BY m.starts DESC,m.id DESC""", (today, member_id))
    return [dict(record, state=membership_state(record, today)) for record in records]


def plan_form(conn, user, data):
    plan_id = integer(data, "id", 0)
    kind = choice(data, "tipo", ("mensual", "periodico", "diario"))
    days = integer(data, "dias", 1, 366)
    cap = integer(data, "tope", 0, 90)
    if kind in ("mensual", "diario") and (cap != 0 or days != {"mensual": 30, "diario": 1}[kind]):
        raise Rule("Mensual dura 30 días y pase diario 1 día. Ambos tienen tope ordinario cero.")
    reason = text(data, "motivo", 4, 160)
    values = (text(data, "nombre", 2, 70), kind, days, integer(data, "precio", 100),
              cap, integer(data, "activo", 0, 1))
    if plan_id:
        old = require(conn, "SELECT * FROM plans WHERE id=?", (plan_id,))
        conn.execute("UPDATE plans SET name=?,kind=?,days=?,price=?,freeze_days=?,active=? WHERE id=?",
                     (*values, plan_id))
        reason += f" | tarifa {old['price']} → {values[3]}"
    else:
        plan_id = conn.execute("INSERT INTO plans(name,kind,days,price,freeze_days,active) VALUES(?,?,?,?,?,?)",
                               values).lastrowid
    audit(conn, user["id"], "guardar plan", "plan", plan_id, reason)
    return {"message": "Plan guardado. Las ventas anteriores conservan su tarifa y condiciones."}


def deal_form(conn, user, data):
    deal_id = integer(data, "id", 0)
    starts, ends = day(data, "inicio"), day(data, "fin")
    if ends < starts:
        raise Rule("El fin del convenio debe ser posterior o igual al inicio.")
    pct = integer(data, "porcentaje", 15, 20)
    if pct not in (15, 20):
        raise Rule("El convenio debe ser de 15% o 20%.")
    values = (text(data, "nombre", 2, 80), pct, str(starts), str(ends), integer(data, "activo", 0, 1))
    if deal_id:
        require(conn, "SELECT id FROM deals WHERE id=?", (deal_id,))
        conn.execute("UPDATE deals SET name=?,pct=?,starts=?,ends=?,active=? WHERE id=?", (*values, deal_id))
    else:
        deal_id = conn.execute("INSERT INTO deals(name,pct,starts,ends,active) VALUES(?,?,?,?,?)", values).lastrowid
    audit(conn, user["id"], "guardar convenio", "convenio", deal_id)
    return {"message": "Convenio guardado."}


def freeze_request(conn, user, data):
    membership_id = integer(data, "membresia")
    record = require(conn, "SELECT * FROM memberships WHERE id=?", (membership_id,))
    start = day(data, "inicio")
    days = integer(data, "dias", 1, 90)
    reason = choice(data, "causa", ("certificado verificado", "viaje"))
    if reason == "viaje" and data.get("viaje_verificado") != "si":
        raise Rule("Confirma que el viaje supera los 15 días.")
    freeze_limit(conn, record, start, days, reason)
    freeze_id = conn.execute("""INSERT INTO freezes(membership_id,starts,ends,days,reason,requester)
         VALUES(?,?,?,?,?,?)""", (membership_id, str(start), str(start + ONE_DAY * (days - 1)), days,
                                  reason, user["id"])).lastrowid
    audit(conn, user["id"], "solicitar", "pausa", freeze_id, reason)
    return {"message": "Solicitud enviada para revisión de Administración."}


def freeze_limit(conn, record, start, days, reason):
    today = str(now().date())
    if record["void"] or not record["starts"] or not record["starts"] <= today <= record["ends"]:
        raise Rule("La membresía debe estar vigente al tramitar la solicitud.")
    if not today <= str(start) <= record["ends"]:
        raise Rule("La pausa debe empezar dentro del período vigente y desde hoy.")
    used = get(conn, """SELECT coalesce(sum(days),0) AS days FROM freezes
                       WHERE membership_id=? AND decision='aprobada'""", (record["id"],))["days"]
    cap = record["freeze_days"]
    if record["kind"] == "mensual" and reason == "certificado verificado":
        cap = 30  # Límite académico de la excepción médica.
    if used + days > cap:
        raise Rule("La pausa excede el tope del plan.")
    if get(conn, """SELECT id FROM freezes WHERE membership_id=? AND decision='aprobada'
                   AND starts<=? AND ends>=?""", (record["id"], str(start + ONE_DAY * (days - 1)), str(start))):
        raise Rule("La pausa se superpone con otra aprobada.")


def freeze_decide(conn, user, data):
    freeze_id = integer(data, "solicitud")
    request = require(conn, "SELECT * FROM freezes WHERE id=?", (freeze_id,))
    if request["decision"] != "pendiente":
        raise Rule("La solicitud ya tiene decisión.", 409)
    decision = choice(data, "decision", ("aprobada", "rechazada"))
    reason = text(data, "motivo", 4, 160)
    if decision == "aprobada":
        record = require(conn, "SELECT * FROM memberships WHERE id=?", (request["membership_id"],))
        freeze_limit(conn, record, date.fromisoformat(request["starts"]), request["days"], request["reason"])
        delta = ONE_DAY * request["days"]
        # Las renovaciones futuras se desplazan junto con el período congelado.
        for future in rows(conn, """SELECT id,starts,ends FROM memberships
                                    WHERE member_id=? AND starts>? AND void=0""", (record["member_id"], record["ends"])):
            conn.execute("UPDATE memberships SET starts=?,ends=? WHERE id=?",
                         (str(date.fromisoformat(future["starts"]) + delta),
                          str(date.fromisoformat(future["ends"]) + delta), future["id"]))
        conn.execute("UPDATE memberships SET ends=? WHERE id=?",
                     (str(date.fromisoformat(record["ends"]) + delta), record["id"]))
    conn.execute("UPDATE freezes SET decision=?,approver=?,decided_reason=? WHERE id=?",
                 (decision, user["id"], reason, freeze_id))
    audit(conn, user["id"], decision, "pausa", freeze_id, reason)
    return {"message": "Decisión registrada."}


def access_result(conn, identifier, date_value):
    member = get(conn, "SELECT id,name FROM members WHERE rut=?", (identifier,))
    if not member:
        return None, False, "RUT no encontrado"
    records = memberships(conn, member["id"], date_value)
    for state in ("Activa", "Congelada", "Pendiente", "Vencida", "Programada"):
        match = next((m for m in records if m["state"] == state), None)
        if match:
            return dict(member, ends=match["ends"]), state == "Activa", {
                "Activa": "Plan vigente", "Congelada": "Plan congelado", "Vencida": "Plan vencido",
                "Pendiente": "Pago pendiente", "Programada": "Plan aún no inicia"}[state]
    return member, False, "Sin plan vigente"


def access_record(conn, user, data, occurred=None):
    identifier = rut(text(data, "rut"))
    at = stamp(occurred)
    member, allowed, reason = access_result(conn, identifier, at[:10])
    source = "en línea"
    if occurred:
        source = "contingencia"
        observed = choice(data, "local", ("permitido", "denegado", "manual"))
        expected = "permitido" if allowed else "denegado"
        if observed != expected:
            source = "contingencia · revisar"
            reason += f" / decisión local: {observed}"
    access_id = conn.execute("INSERT INTO access(member_id,user_id,at,allowed,reason,source) VALUES(?,?,?,?,?,?)",
                             (member["id"] if member else None, user["id"], at, int(allowed), reason, source)).lastrowid
    if not allowed or "revisar" in source:
        audit(conn, user["id"], "acceso denegado" if not allowed else "acceso a revisar", "acceso", access_id, reason)
    return {"id": access_id, "allowed": allowed, "member": member, "reason": reason, "source": source, "at": at,
            "message": ("Permitido. " if allowed else "Denegado. ") + reason}


def lease_key(conn):
    return bytes.fromhex(require(conn, "SELECT value FROM settings WHERE key='lease_key'")["value"])


def offline_snapshot(conn, user):
    issued = int(now().timestamp())
    expiry = issued + 1800
    body = f"{user['id']}.{issued}.{expiry}"
    tag = hmac.new(lease_key(conn), body.encode(), hashlib.sha256).hexdigest()
    today = str(now().date())
    return {
        "lease": body + "." + tag, "expires": expiry, "issued": issued,
        "members": rows(conn, "SELECT rut FROM members"),
        "periods": rows(conn, """SELECT s.rut,m.id,m.starts,m.ends FROM memberships m JOIN members s
                                 ON s.id=m.member_id WHERE m.void=0 AND m.ends>=?""", (today,)),
        "freezes": rows(conn, """SELECT membership_id,starts,ends FROM freezes
                                 WHERE decision='aprobada' AND ends>=?""", (today,)),
        "plans": rows(conn, "SELECT id,name,price FROM plans WHERE active=1 ORDER BY id"),
    }


def command(conn, user, data, action, handler):
    command_id = text(data, "command", 36, 36)
    if not re.fullmatch(r"[0-9a-f-]{36}", command_id):
        raise Rule("Identificador de operación inválido.")
    digest = hashlib.sha256(json.dumps([action, data], sort_keys=True).encode()).hexdigest()
    saved = get(conn, "SELECT * FROM commands WHERE id=?", (command_id,))
    if saved:
        if saved["user_id"] != user["id"] or not hmac.compare_digest(saved["payload_hash"], digest):
            raise Rule("La clave de operación ya está ocupada por otra solicitud.", 409)
        return dict(json.loads(saved["result"]), replay=True)
    occurred = None
    if data.get("lease"):
        parts = text(data, "lease", 10, 140).split(".")
        if len(parts) != 4 or not all(p.isascii() and p.isdigit() for p in parts[:3]):
            raise Rule("Permiso de contingencia inválido.", 403)
        signature = hmac.new(lease_key(conn), ".".join(parts[:3]).encode(), hashlib.sha256).hexdigest()
        actor, issued, expiry = map(int, parts[:3])
        captured = integer(data, "capturado", 1_000_000_000, 4_000_000_000)
        server_time = int(now().timestamp())
        if actor != user["id"] or not hmac.compare_digest(parts[3], signature):
            raise Rule("La operación pertenece a otra cuenta o permiso inválido.", 403)
        if not issued - 5 <= captured <= min(expiry, server_time + 10) or server_time - captured > 604800:
            raise Rule("Fuera de la ventana de contingencia. Concilia el registro antes de descartarlo.", 409)
        occurred = datetime.fromtimestamp(captured, timezone.utc).astimezone()
    result = handler(conn, user, data, occurred)
    conn.execute("INSERT INTO commands VALUES(?,?,?,?)",
                 (command_id, user["id"], digest, json.dumps(result, ensure_ascii=False)))
    return result
