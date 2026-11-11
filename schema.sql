PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, password TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('admin','recepcion','entrenador')),
 active INTEGER NOT NULL DEFAULT 1, failed INTEGER NOT NULL DEFAULT 0, locked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
 token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
 csrf TEXT NOT NULL, touched INTEGER NOT NULL, expires INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
 id INTEGER PRIMARY KEY, rut TEXT NOT NULL UNIQUE, name TEXT NOT NULL, born TEXT,
 address TEXT NOT NULL, phone TEXT NOT NULL, email TEXT NOT NULL, emergency TEXT NOT NULL,
 emergency_phone TEXT NOT NULL, notice_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
 days INTEGER NOT NULL CHECK(days BETWEEN 1 AND 366), price INTEGER NOT NULL CHECK(price>0),
 freeze_days INTEGER NOT NULL CHECK(freeze_days BETWEEN 0 AND 90), active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS deals (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, pct INTEGER NOT NULL CHECK(pct IN (15,20)),
 starts TEXT NOT NULL, ends TEXT NOT NULL, active INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS memberships (
 id INTEGER PRIMARY KEY, member_id INTEGER NOT NULL REFERENCES members(id),
 plan_id INTEGER NOT NULL REFERENCES plans(id), plan_name TEXT NOT NULL,
 kind TEXT NOT NULL, days INTEGER NOT NULL, freeze_days INTEGER NOT NULL,
 starts TEXT, ends TEXT, void INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS memberships_member ON memberships(member_id, starts, ends);
CREATE TABLE IF NOT EXISTS payments (
 id INTEGER PRIMARY KEY, membership_id INTEGER NOT NULL UNIQUE REFERENCES memberships(id),
 amount INTEGER NOT NULL CHECK(amount>0), method TEXT NOT NULL CHECK(method IN ('efectivo','tarjeta','transferencia')),
 reference TEXT, user_id INTEGER NOT NULL REFERENCES users(id), at TEXT NOT NULL,
 concept TEXT NOT NULL, deal_id INTEGER REFERENCES deals(id), UNIQUE(method,reference)
);
CREATE TABLE IF NOT EXISTS settlements (
 payment_id INTEGER PRIMARY KEY REFERENCES payments(id), user_id INTEGER NOT NULL REFERENCES users(id), at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
 id INTEGER PRIMARY KEY, payment_id INTEGER NOT NULL REFERENCES payments(id),
 user_id INTEGER NOT NULL REFERENCES users(id), at TEXT NOT NULL, reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS adjustments (
 id INTEGER PRIMARY KEY, payment_id INTEGER NOT NULL REFERENCES payments(id),
 amount INTEGER NOT NULL CHECK(amount<0), user_id INTEGER NOT NULL REFERENCES users(id),
 at TEXT NOT NULL, reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS freezes (
 id INTEGER PRIMARY KEY, membership_id INTEGER NOT NULL REFERENCES memberships(id),
 starts TEXT NOT NULL, ends TEXT NOT NULL, days INTEGER NOT NULL, reason TEXT NOT NULL,
 requester INTEGER NOT NULL REFERENCES users(id), approver INTEGER REFERENCES users(id),
 decision TEXT NOT NULL DEFAULT 'pendiente', decided_reason TEXT
);
CREATE TABLE IF NOT EXISTS access (
 id INTEGER PRIMARY KEY, member_id INTEGER REFERENCES members(id),
 user_id INTEGER NOT NULL REFERENCES users(id), at TEXT NOT NULL, allowed INTEGER NOT NULL,
 reason TEXT NOT NULL, source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS access_at ON access(at,allowed);
CREATE TABLE IF NOT EXISTS closures (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
 day TEXT NOT NULL, shift TEXT NOT NULL, closed_by INTEGER NOT NULL REFERENCES users(id),
 at TEXT NOT NULL, snapshot TEXT NOT NULL, UNIQUE(user_id,day,shift)
);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), at TEXT NOT NULL,
 event TEXT NOT NULL, entity TEXT NOT NULL, target INTEGER, detail TEXT NOT NULL, origin TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS commands (
 id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), payload_hash TEXT NOT NULL,
 result TEXT NOT NULL
);
CREATE VIEW IF NOT EXISTS ledger AS
 SELECT p.id AS payment_id, s.user_id, s.at, p.method, p.concept, p.amount, 'Cobro' AS movement
 FROM settlements s JOIN payments p ON p.id=s.payment_id
 UNION ALL
 SELECT p.id, a.user_id, a.at, p.method, p.concept, a.amount, 'Ajuste'
 FROM adjustments a JOIN payments p ON p.id=a.payment_id;
