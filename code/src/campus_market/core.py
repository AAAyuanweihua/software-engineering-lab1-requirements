"""Shared authorization, transaction, idempotency and audit infrastructure."""

import hashlib
import json
import logging
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta

from campus_market.database import Database
from campus_market.models import MarketError, User, new_id, now, required
from campus_market.security import hash_password, new_token, token_digest, verify_password
from campus_market.validation import validated

Operation = Callable[[sqlite3.Connection, User], dict]
IDEMPOTENT_ACTIONS = {
    "RESERVE",
    "SELLER_DECISION",
    "CANCEL_PENDING",
    "REQUEST_CANCEL",
    "DECIDE_CANCEL",
    "CONFIRM",
}
LOGGER = logging.getLogger(__name__)


class Core:
    """Service base: every write reauthenticates inside its serialization boundary."""

    def __init__(self, database: Database, fault: Callable[[str], None] | None = None):
        self.database = database
        self.fault = fault

    def checkpoint(self, name: str) -> None:
        """Explicit fault seam for tests, never available as a CLI command."""
        if self.fault is not None:
            self.fault(name)

    @staticmethod
    def one(connection: sqlite3.Connection, sql: str, args: tuple = ()) -> dict:
        """Read one required row or raise a non-leaking business error."""
        row = connection.execute(sql, args).fetchone()
        if row is None:
            raise MarketError("NOT_FOUND", "记录不存在", 404)
        return dict(row)

    @staticmethod
    def require(condition: bool, code: str, message: str, status: int = 409) -> None:
        """Keep preconditions visible at each business transition."""
        if not condition:
            raise MarketError(code, message, status)

    def identity(self, connection: sqlite3.Connection, token: str) -> User:
        """Re-read identity and enabled status, not a caller-supplied role object."""
        row = connection.execute(
            "SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id "
            "WHERE s.token_hash=? AND s.expires_at>?",
            (token_digest(token), now()),
        ).fetchone()
        self.require(row is not None, "UNAUTHENTICATED", "请先登录或重新登录", 401)
        self.require(bool(row["enabled"]), "DISABLED", "账号已停用，请联系管理员", 403)
        return User(row["id"], row["account"], bool(row["enabled"]), bool(row["admin"]))

    @staticmethod
    def audit(connection, actor_id, action, object_id, outcome, detail) -> None:
        """Append sanitized state information; callers never pass credentials."""
        connection.execute(
            "INSERT INTO audit_logs VALUES (?,?,?,?,?,?,?)",
            (
                new_id(),
                actor_id,
                action,
                object_id,
                outcome,
                json.dumps(detail, ensure_ascii=False, sort_keys=True),
                now(),
            ),
        )

    @staticmethod
    def audit_context(connection, object_id):
        """Find aggregate members before mutation so closed rows remain auditable afterward."""
        product_ids, reservation_ids, cancellation_ids = set(), set(), set()
        if connection.execute("SELECT 1 FROM products WHERE id=?", (object_id,)).fetchone():
            product_ids.add(object_id)
        reservation = connection.execute(
            "SELECT id,product_id FROM reservations WHERE id=?", (object_id,)
        ).fetchone()
        cancellation = connection.execute(
            "SELECT id,reservation_id FROM cancellations WHERE id=?", (object_id,)
        ).fetchone()
        if cancellation:
            cancellation_ids.add(cancellation["id"])
            reservation = connection.execute(
                "SELECT id,product_id FROM reservations WHERE id=?",
                (cancellation["reservation_id"],),
            ).fetchone()
        if reservation:
            reservation_ids.add(reservation["id"])
            product_ids.add(reservation["product_id"])
        for product_id in product_ids:
            reservation_ids.update(
                row[0]
                for row in connection.execute(
                    "SELECT id FROM reservations WHERE product_id=? "
                    "AND status IN ('PENDING','CONFIRMED','CANCEL_PENDING','COMPLETED')",
                    (product_id,),
                )
            )
        for reservation_id in reservation_ids:
            cancellation_ids.update(
                row[0]
                for row in connection.execute(
                    "SELECT id FROM cancellations WHERE reservation_id=? AND status='PENDING'",
                    (reservation_id,),
                )
            )
        return {
            "products": product_ids,
            "reservations": reservation_ids,
            "cancellations": cancellation_ids,
        }

    @classmethod
    def snapshot(cls, connection, object_id, context=None):
        """Capture audit state without password hashes, sessions or business descriptions."""
        state = {}
        columns = {
            "products": "id,status,version,restricted",
            "reservations": (
                "id,product_id,status,epoch,buyer_confirmed,seller_confirmed,completed_at"
            ),
            "cancellations": "id,reservation_id,status",
            "users": "id,enabled,admin",
        }
        for table, selection in columns.items():
            row = connection.execute(
                f"SELECT {selection} FROM {table} WHERE id=?", (object_id,)
            ).fetchone()
            if row:
                state[table] = dict(row)
        current = cls.audit_context(connection, object_id)
        for table, ids in current.items():
            ids.update((context or {}).get(table, ()))
            related = {}
            for record_id in sorted(ids):
                row = connection.execute(
                    f"SELECT {columns[table]} FROM {table} WHERE id=?", (record_id,)
                ).fetchone()
                if row:
                    related[record_id] = dict(row)
            if related:
                state["related_" + table] = related
                if table == "products" and "products" not in state:
                    state["products"] = next(iter(related.values()))
        return state

    def failure(self, actor_id, action, object_id, code):
        """Bound audit lock waits; explicitly signal unpersisted audit without replacing errors."""
        diagnostic_id = new_id()
        for attempt in range(2):
            try:
                with closing(self.database.connect()) as connection:
                    connection.execute("PRAGMA busy_timeout=100")
                    connection.execute("BEGIN IMMEDIATE")
                    self.audit(
                        connection,
                        actor_id,
                        action,
                        object_id,
                        "FAILURE",
                        {"code": code, "diagnostic_id": diagnostic_id},
                    )
                    connection.commit()
                return {"status": "persisted", "diagnostic_id": diagnostic_id}
            except sqlite3.Error as exc:
                error_type = type(exc).__name__
                if attempt == 0:
                    time.sleep(0.02)
        # No SQL, input payload or exception message is included in this diagnostic.
        LOGGER.error(
            "AUDIT_UNAVAILABLE %s",
            json.dumps(
                {
                    "diagnostic_id": diagnostic_id,
                    "action": action,
                    "object_id": object_id,
                    "business_code": code,
                    "audit_exception_type": error_type,
                },
                ensure_ascii=False,
            ),
        )
        return {"status": "unavailable", "diagnostic_id": diagnostic_id}

    def failed_error(self, error, actor_id, action, object_id):
        """Attach truthful audit status while preserving the original business error code."""
        audit = self.failure(actor_id, action, object_id, error.code)
        error.audit_status = audit["status"]
        error.diagnostic_id = audit["diagnostic_id"]
        return error

    def write(
        self,
        token: str,
        action: str,
        object_id: str,
        payload: dict,
        operation: Operation,
        key: str | None = None,
        admin: bool = False,
    ) -> dict:
        """Serialize business writes; receipts and successful audits commit atomically."""
        if action in IDEMPOTENT_ACTIONS:
            required(key, "请求键", 100)
        actor_id = None
        signature = hashlib.sha256(
            json.dumps([action, object_id, payload], sort_keys=True).encode()
        ).hexdigest()
        try:
            with closing(self.database.connect()) as connection:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    actor = self.identity(connection, token)
                    actor_id = actor.id
                    self.require(not admin or actor.admin, "FORBIDDEN", "仅管理员可操作", 403)
                    if key is not None:
                        required(key, "请求键", 100)
                        previous = connection.execute(
                            "SELECT * FROM receipts WHERE actor_id=? AND request_key=?",
                            (actor.id, key),
                        ).fetchone()
                        if previous:
                            self.require(
                                previous["payload_hash"] == signature,
                                "KEY_CONFLICT",
                                "同一请求键不能用于不同参数",
                            )
                            connection.commit()
                            return json.loads(previous["result"])
                    context = self.audit_context(connection, object_id)
                    before = self.snapshot(connection, object_id, context)
                    result = operation(connection, actor)
                    target = object_id or result.get("id", "")
                    detail = {
                        "before": before,
                        "after": self.snapshot(connection, target, context),
                    }
                    if "reason" in result:
                        detail["reason"] = result["reason"]
                    self.audit(connection, actor.id, action, target, "SUCCESS", detail)
                    if key is not None:
                        connection.execute(
                            "INSERT INTO receipts VALUES (?,?,?,?)",
                            (actor.id, key, signature, json.dumps(result, ensure_ascii=False)),
                        )
                    self.checkpoint("before_commit")
                    connection.commit()
                    return result
                except Exception:
                    connection.rollback()
                    raise
        except MarketError as exc:
            self.failed_error(exc, actor_id, action, object_id)
            raise
        except sqlite3.Error as exc:
            code = "CONSTRAINT_CONFLICT" if isinstance(exc, sqlite3.IntegrityError) else "DB_ERROR"
            error = MarketError(code, "数据库操作未生效，请刷新后重试", 503)
            raise self.failed_error(error, actor_id, action, object_id) from exc
        except Exception as exc:
            error = MarketError("INTERNAL_ERROR", "操作未生效，请按诊断编号联系维护人员", 500)
            self.failed_error(error, actor_id, action, object_id)
            LOGGER.error(
                "OPERATION_FAILED %s",
                json.dumps(
                    {
                        "diagnostic_id": error.diagnostic_id,
                        "action": action,
                        "exception_type": type(exc).__name__,
                    }
                ),
            )
            raise error from exc

    @validated
    def register(self, account: str, password: str, bootstrap: bool = False) -> str:
        """Ordinary registration cannot select admin; first-admin bootstrap is local-only."""
        account = required(account, "账号", 60)
        encoded = hash_password(password)
        with closing(self.database.connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                if bootstrap:
                    count = connection.execute(
                        "SELECT count(*) FROM users WHERE admin=1"
                    ).fetchone()[0]
                    self.require(count == 0, "ADMIN_EXISTS", "首个管理员已经初始化")
                user_id = new_id()
                connection.execute(
                    "INSERT INTO users(id,account,password_hash,admin) VALUES (?,?,?,?)",
                    (user_id, account, encoded, int(bootstrap)),
                )
                self.audit(
                    connection, user_id, "REGISTER", user_id, "SUCCESS", {"admin": bootstrap}
                )
                connection.commit()
                return user_id
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise MarketError("ACCOUNT_EXISTS", "账号已存在", 409) from exc

    @validated
    def create_admin(self, token: str, account: str, password: str) -> dict:
        """An existing authenticated administrator may create another controlled account."""
        account = required(account, "账号", 60)
        encoded = hash_password(password)

        def operation(connection, _actor):
            user_id = new_id()
            connection.execute(
                "INSERT INTO users(id,account,password_hash,admin) VALUES (?,?,?,1)",
                (user_id, account, encoded),
            )
            return {"id": user_id, "account": account, "admin": True}

        return self.write(token, "CREATE_ADMIN", account, {}, operation, admin=True)

    @validated
    def login(self, account: str, password: str) -> str:
        """Return a twelve-hour opaque session; disabled accounts cannot sign in."""
        with closing(self.database.connect()) as connection:
            row = connection.execute("SELECT * FROM users WHERE account=?", (account,)).fetchone()
            # A fixed-format dummy hash keeps password derivation work on unknown accounts.
            dummy = "pbkdf2_sha256$600000$" + "00" * 16 + "$" + "00" * 32
            valid = verify_password(password, row["password_hash"] if row else dummy)
            if row is None or not valid or not row["enabled"]:
                self.failure(None, "LOGIN", "", "BAD_CREDENTIALS")
                raise MarketError("BAD_CREDENTIALS", "账号、密码或账号状态不正确", 401)
            token = new_token()
            expiry = (datetime.now(UTC) + timedelta(hours=12)).isoformat(timespec="microseconds")
            connection.execute(
                "INSERT INTO sessions VALUES (?,?,?)", (token_digest(token), row["id"], expiry)
            )
            return token

    @validated
    def logout(self, token: str) -> None:
        """Invalidate the current session rather than just hiding CLI state."""
        try:
            with closing(self.database.connect()) as connection:
                connection.execute(
                    "DELETE FROM sessions WHERE token_hash=?", (token_digest(token),)
                )
        except sqlite3.Error as exc:
            raise MarketError(
                "DB_ERROR", "会话注销未持久化，请稍后重试；本地身份已清除", 503
            ) from exc

    @validated
    def me(self, token: str) -> User:
        """Return current account identity to presentation adapters."""
        with closing(self.database.connect()) as connection:
            return self.identity(connection, token)
