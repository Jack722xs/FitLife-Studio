"""Cobranza, conciliación, cierre de caja e indicadores derivados del libro."""
import json
from datetime import date, timedelta

from operations import ONE_DAY
from storage import METHODS, Rule, audit, choice, day, get, integer, now, require, rows, rut, stamp, text


def shift(at):
    return "AM" if at[11:13] < "12" else "PM"


def ensure_open(conn, cashier, at):
    closed = get(conn, """SELECT id FROM closures WHERE day=? AND
                         (shift='Día' OR (user_id=? AND shift=?))""", (at[:10], cashier, shift(at)))
    if closed:
        raise Rule("La caja del movimiento ya está cerrada. Administración debe conciliarlo.", 409)


def settle(conn, payment, record, user_id, at):
    ensure_open(conn, user_id, at)
    latest = get(conn, "SELECT max(ends) AS ends FROM memberships WHERE member_id=? AND void=0",
                 (record["member_id"],))["ends"]
    today = date.fromisoformat(at[:10])
    starts = max(today, date.fromisoformat(latest) + ONE_DAY) if latest else today
    if record["kind"] == "diario" and starts != today:
        raise Rule("El Pase Diario se consume hoy. Ya existe un período vigente o programado.", 409)
    conn.execute("INSERT INTO settlements VALUES(?,?,?)", (payment["id"], user_id, at))
    conn.execute("UPDATE memberships SET starts=?,ends=? WHERE id=?",
                 (str(starts), str(starts + ONE_DAY * (record["days"] - 1)), record["id"]))
    return starts


def sell(conn, user, data, occurred=None):
    identifier = rut(text(data, "rut"))
    member = require(conn, "SELECT id FROM members WHERE rut=?", (identifier,))
    plan = require(conn, "SELECT * FROM plans WHERE id=? AND active=1", (integer(data, "plan"),))
    method = choice(data, "medio", METHODS)
    reference = text(data, "referencia", 3, 40) if method != "efectivo" else None
    if reference and (not all(c.isascii() and (c.isalnum() or c in "-_") for c in reference)
                      or reference.isdigit() and 12 <= len(reference) <= 19):
        raise Rule("Usa solo una referencia POS/bancaria. Evita números de tarjeta.")
    at = stamp(occurred)
    deal_id = integer(data, "convenio", 0)
    price = plan["price"]
    if deal_id:
        deal = require(conn, "SELECT * FROM deals WHERE id=? AND active=1 AND ? BETWEEN starts AND ends",
                       (deal_id, at[:10]))
        if plan["kind"] != "mensual" or occurred:
            raise Rule("Los convenios se aplican a planes mensuales en línea.")
        price = (price * (100 - deal["pct"]) + 50) // 100
    if price != integer(data, "cotizacion", 1):
        raise Rule("La tarifa cambió. Verifica el monto con Administración.", 409)
    ensure_open(conn, user["id"], at)
    if get(conn, "SELECT id FROM memberships WHERE member_id=? AND starts IS NULL AND void=0", (member["id"],)):
        raise Rule("Existe una transferencia pendiente para este socio. Resuélvela primero.", 409)
    if plan["kind"] == "diario" and method == "transferencia":
        raise Rule("Para Pase Diario usa efectivo o POS con confirmación inmediata.")
    concept = "Pase diario" if plan["kind"] == "diario" else ("Renovación" if get(
        conn, "SELECT id FROM memberships WHERE member_id=? AND starts IS NOT NULL AND void=0 LIMIT 1",
        (member["id"],)) else "Inscripción")
    member_plan = conn.execute("""INSERT INTO memberships(member_id,plan_id,plan_name,kind,days,freeze_days)
                                VALUES(?,?,?,?,?,?)""", (member["id"], plan["id"], plan["name"],
                                                        plan["kind"], plan["days"], plan["freeze_days"])).lastrowid
    payment_id = conn.execute("""INSERT INTO payments(membership_id,amount,method,reference,user_id,at,concept,deal_id)
                               VALUES(?,?,?,?,?,?,?,?)""", (member_plan, price, method, reference, user["id"],
                                                             at, concept, deal_id or None)).lastrowid
    message = "Transferencia pendiente. Confirma el abono para activar el plan."
    if method != "transferencia":
        settle(conn, {"id": payment_id}, require(conn, "SELECT * FROM memberships WHERE id=?", (member_plan,)),
               user["id"], at)
        message = "Pago confirmado y vigencia registrada."
    audit(conn, user["id"], "registrar", "pago", payment_id, method, "contingencia" if occurred else "local")
    return {"id": payment_id, "message": message}


def confirm(conn, user, data):
    payment = require(conn, "SELECT * FROM payments WHERE id=?", (integer(data, "pago"),))
    member_plan = require(conn, "SELECT * FROM memberships WHERE id=?", (payment["membership_id"],))
    if payment["method"] != "transferencia" or member_plan["starts"] or member_plan["void"]:
        raise Rule("La transferencia no está pendiente.", 409)
    if data.get("verificado") != "si":
        raise Rule("Confirma que verificaste el abono externo.")
    settle(conn, payment, member_plan, user["id"], stamp())
    audit(conn, user["id"], "confirmar", "pago", payment["id"], "abono verificado")
    return {"message": "Transferencia confirmada y vigencia actualizada."}


def payment_note(conn, user, data):
    payment = require(conn, "SELECT id FROM payments WHERE id=?", (integer(data, "pago"),))
    reason = text(data, "motivo", 4, 160)
    conn.execute("INSERT INTO notes(payment_id,user_id,at,reason) VALUES(?,?,?,?)",
                 (payment["id"], user["id"], stamp(), reason))
    audit(conn, user["id"], "observación", "pago", payment["id"], reason)
    return {"message": "Observación guardada. El pago original permanece."}


def adjust(conn, user, data):
    payment = require(conn, """SELECT p.*,m.void FROM payments p JOIN memberships m
                              ON m.id=p.membership_id WHERE p.id=?""", (integer(data, "pago"),))
    reason = text(data, "motivo", 4, 160)
    amount = integer(data, "monto", 0)
    if payment["void"]:
        raise Rule("El pago ya se encuentra anulado.", 409)
    if not get(conn, "SELECT 1 FROM settlements WHERE payment_id=?", (payment["id"],)):
        if amount:
            raise Rule("Para cancelar una transferencia sin abono, ingresa monto 0.")
        conn.execute("UPDATE memberships SET void=1 WHERE id=?", (payment["membership_id"],))
    else:
        previous = get(conn, "SELECT coalesce(sum(amount),0) AS amount FROM adjustments WHERE payment_id=?",
                       (payment["id"],))["amount"]
        remainder = payment["amount"] + previous
        if not 1 <= amount <= remainder:
            raise Rule(f"El ajuste debe ser mayor que cero y hasta {remainder} CLP.")
        at = stamp()
        ensure_open(conn, user["id"], at)
        conn.execute("INSERT INTO adjustments(payment_id,amount,user_id,at,reason) VALUES(?,?,?,?,?)",
                     (payment["id"], -amount, user["id"], at, reason))
        if amount == remainder:
            conn.execute("UPDATE memberships SET void=1 WHERE id=?", (payment["membership_id"],))
    audit(conn, user["id"], "ajustar", "pago", payment["id"], reason)
    return {"message": "Corrección registrada. Se preservó el registro original."}


def member_payments(conn, member_id):
    return rows(conn, """SELECT p.*,m.plan_name,m.void,u.name AS operator,s.at AS confirmed,
                  p.amount+coalesce((SELECT sum(a.amount) FROM adjustments a WHERE a.payment_id=p.id),0) AS net
                 FROM payments p JOIN memberships m ON m.id=p.membership_id JOIN users u ON u.id=p.user_id
                 LEFT JOIN settlements s ON s.payment_id=p.id WHERE m.member_id=? ORDER BY p.id DESC""", (member_id,))


def summary(movements):
    methods = dict.fromkeys(METHODS, 0)
    concepts = {}
    for move in movements:
        methods[move["method"]] += move["amount"]
        concepts[move["concept"]] = concepts.get(move["concept"], 0) + move["amount"]
    return {"methods": methods, "concepts": concepts, "net": sum(methods.values())}


def cash_view(conn, user, data):
    date_value = str(day(data, "fecha"))
    turn = choice(data, "turno", ("AM", "PM", "Día"))
    cashier = integer(data, "cajero", 0)
    if user["role"] != "admin" and (cashier != user["id"] or turn == "Día"):
        raise Rule("Recepción solo puede consultar y cerrar su turno.", 403)
    if turn == "Día" and cashier != 0 or turn != "Día" and cashier == 0:
        raise Rule("Para consolidar el día selecciona todos los cajeros.")
    if cashier:
        require(conn, "SELECT id FROM users WHERE id=?",
                (cashier,))
    saved = get(conn, "SELECT * FROM closures WHERE day=? AND shift=? AND user_id=?",
                (date_value, turn, cashier or user["id"])) if turn != "Día" else get(
                    conn, "SELECT * FROM closures WHERE day=? AND shift='Día'", (date_value,))
    if saved:
        return dict(json.loads(saved["snapshot"]), closed=True, folio=saved["id"])
    daily_lock = bool(get(conn, "SELECT id FROM closures WHERE day=? AND shift='Día'", (date_value,)))
    movements = rows(conn, """SELECT l.*,u.name AS operator FROM ledger l JOIN users u ON u.id=l.user_id
        WHERE substr(l.at,1,10)=? AND (?=0 OR l.user_id=?) ORDER BY l.at,l.payment_id""",
        (date_value, cashier, cashier))
    if turn != "Día":
        movements = [row for row in movements if shift(row["at"]) == turn]
    pending = rows(conn, """SELECT p.id,p.at,p.amount FROM payments p JOIN memberships m ON m.id=p.membership_id
        LEFT JOIN settlements s ON s.payment_id=p.id
        WHERE p.method='transferencia' AND s.payment_id IS NULL AND m.void=0
        AND substr(p.at,1,10)<=? AND (?=0 OR p.user_id=?) ORDER BY p.at""", (date_value, cashier, cashier))
    return dict(summary(movements), moves=movements, pending=pending, locked=daily_lock, closed=False)


def close_cash(conn, user, data):
    date_value = str(day(data, "fecha"))
    turn = choice(data, "turno", ("AM", "PM", "Día"))
    cashier = integer(data, "cajero", 0)
    current = stamp()
    if date_value > current[:10] or (date_value == current[:10] and turn == "AM" and shift(current) == "AM"):
        raise Rule("El turno seleccionado aún no termina.")
    result = cash_view(conn, user, data)
    if result["closed"] or result["locked"]:
        raise Rule("La caja ya está cerrada.", 409)
    if turn == "Día":
        open_shifts = {(m["user_id"], shift(m["at"])) for m in result["moves"]} - {
            (c["user_id"], c["shift"]) for c in rows(conn, "SELECT user_id,shift FROM closures WHERE day=?",
                                                   (date_value,))}
        if open_shifts:
            raise Rule("Cierra primero los turnos con movimientos.")
    if data.get("sin_cola") != "si":
        raise Rule("Confirma que se conciliaron las colas de contingencia del puesto.")
    counted = integer(data, "contado", 0)
    difference = counted - result["methods"]["efectivo"]
    reason = text(data, "motivo", 4 if difference else 0, 160)
    result.update(counted=counted, difference=difference, reason=reason)
    closure = conn.execute("""INSERT INTO closures(user_id,day,shift,closed_by,at,snapshot)
                             VALUES(?,?,?,?,?,?)""", (cashier or user["id"], date_value, turn, user["id"], current,
                                                       json.dumps(result, ensure_ascii=False))).lastrowid
    audit(conn, user["id"], "cerrar", "caja", closure, reason)
    return {"message": f"Cierre #{closure} guardado. Diferencia {difference} CLP."}


def reports(conn):
    today = now().date()
    previous_week = str(today - timedelta(days=7))
    next_week = str(today + timedelta(days=7))
    next_three = str(today + timedelta(days=3))
    deadlines = rows(conn, """SELECT s.id,s.name,s.rut,max(m.ends) AS due
        FROM memberships m JOIN members s ON s.id=m.member_id
        WHERE m.void=0 AND m.ends IS NOT NULL GROUP BY s.id HAVING due BETWEEN ? AND ? ORDER BY due""",
        (previous_week, next_week))
    cohorts = rows(conn, """SELECT m.member_id,m.ends,EXISTS(SELECT 1 FROM memberships n
        WHERE n.member_id=m.member_id AND n.id<>m.id AND n.void=0 AND n.starts=date(m.ends,'+1 day')) AS renewed
        FROM memberships m WHERE m.void=0 AND m.ends>=? AND m.ends<?""", (previous_week, str(today)))
    month = today.replace(day=1)
    last = (month - ONE_DAY).replace(day=1)
    amounts = rows(conn, """SELECT substr(at,1,7) AS month,method,concept,sum(amount) AS amount
                 FROM ledger WHERE substr(at,1,7) IN (?,?) GROUP BY 1,2,3 ORDER BY 1 DESC,2,3""",
                   (str(last)[:7], str(month)[:7]))
    attendance = rows(conn, """SELECT substr(at,1,10) AS day,substr(at,12,2) AS hour,count(*) AS count
        FROM access WHERE allowed=1 AND substr(at,1,7)=? GROUP BY 1,2 ORDER BY 1 DESC,2""", (str(month)[:7],))
    return {"expired": [r for r in deadlines if r["due"] < str(today)],
            "upcoming": [r for r in deadlines if next_three <= r["due"] <= next_week],
            "renewed": sum(row["renewed"] for row in cohorts), "cohort": len(cohorts),
            "income": amounts, "attendance": attendance}

