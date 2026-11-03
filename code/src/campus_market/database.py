"""SQLite schema and connection factory, with constraints as a concurrency backstop."""

import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, account TEXT NOT NULL UNIQUE,
 password_hash TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
 admin INTEGER NOT NULL DEFAULT 0 CHECK(admin IN (0,1))
);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS products (
 id TEXT PRIMARY KEY, seller_id TEXT NOT NULL REFERENCES users(id),
 title TEXT NOT NULL, category TEXT NOT NULL REFERENCES categories(name),
 description TEXT NOT NULL, image_ref TEXT NOT NULL,
 price_cents INTEGER NOT NULL CHECK(price_cents BETWEEN 1 AND 9999999),
 status TEXT NOT NULL CHECK(status IN
 ('DRAFT','REVIEWING','PUBLISHED','RESERVED','CONFIRMED','COMPLETED','OFFLINE')),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 restricted INTEGER NOT NULL DEFAULT 0 CHECK(restricted IN (0,1)),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, published_at TEXT
);
CREATE TABLE IF NOT EXISTS reservations (
 id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id),
 buyer_id TEXT NOT NULL REFERENCES users(id),
 price_cents INTEGER NOT NULL CHECK(price_cents BETWEEN 1 AND 9999999),
 status TEXT NOT NULL CHECK(status IN
 ('PENDING','CONFIRMED','CANCEL_PENDING','CANCELLED','REJECTED','CLOSED','COMPLETED')),
 buyer_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(buyer_confirmed IN (0,1)),
 seller_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(seller_confirmed IN (0,1)),
 epoch INTEGER NOT NULL DEFAULT 1 CHECK(epoch>0),
 created_at TEXT NOT NULL, completed_at TEXT,
 CHECK((status='COMPLETED')=(completed_at IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_reservation
 ON reservations(product_id) WHERE status IN ('PENDING','CONFIRMED','CANCEL_PENDING');
CREATE TABLE IF NOT EXISTS cancellations (
 id TEXT PRIMARY KEY, reservation_id TEXT NOT NULL REFERENCES reservations(id),
 reason TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('PENDING','APPROVED','REJECTED','WITHDRAWN','CLOSED')),
 created_at TEXT NOT NULL, decided_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_cancellation
 ON cancellations(reservation_id) WHERE status='PENDING';
CREATE TABLE IF NOT EXISTS reviews (
 id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id),
 actor_id TEXT NOT NULL REFERENCES users(id), version INTEGER NOT NULL,
 decision TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_logs (
 id TEXT PRIMARY KEY, actor_id TEXT REFERENCES users(id), action TEXT NOT NULL,
 object_id TEXT NOT NULL, outcome TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS immutable_logs_update BEFORE UPDATE ON audit_logs
 BEGIN SELECT RAISE(ABORT,'audit logs are append-only'); END;
CREATE TRIGGER IF NOT EXISTS immutable_logs_delete BEFORE DELETE ON audit_logs
 BEGIN SELECT RAISE(ABORT,'audit logs are append-only'); END;
CREATE TABLE IF NOT EXISTS receipts (
 actor_id TEXT NOT NULL REFERENCES users(id), request_key TEXT NOT NULL,
 payload_hash TEXT NOT NULL, result TEXT NOT NULL,
 PRIMARY KEY(actor_id,request_key)
);
CREATE INDEX IF NOT EXISTS product_filter
 ON products(status,category,published_at,id);
CREATE INDEX IF NOT EXISTS buyer_reservations ON reservations(buyer_id,status);
CREATE INDEX IF NOT EXISTS audit_filter ON audit_logs(created_at,actor_id,action);
"""


@contextmanager
def read_transaction(database):
    """Keep authorization and all SELECTs for one response in one short WAL snapshot."""
    with closing(database.connect()) as connection:
        connection.execute("BEGIN")
        try:
            yield connection
        except Exception:
            connection.execute("ROLLBACK")
            raise
        else:
            connection.execute("COMMIT")


class Database:
    """Each operation opens its own connection; no shared cross-thread connection."""

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)
            connection.executemany(
                "INSERT OR IGNORE INTO categories(name) VALUES (?)",
                [(name,) for name in ("教材", "数码", "生活", "运动", "其他")],
            )

    def connect(self) -> sqlite3.Connection:
        """Use explicit transaction boundaries and enforce foreign keys everywhere."""
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection
