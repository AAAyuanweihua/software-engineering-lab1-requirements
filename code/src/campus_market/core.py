"""Shared authorization, transaction, idempotency and audit infrastructure."""

import hashlib
import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta

from campus_market.database import Database
from campus_market.models import MarketError, User, new_id, now, required
from campus_market.security import hash_password, new_token, token_digest, verify_password

Operation = Callable[[sqlite3.Connection, User], dict]


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

    def failure(self, actor_id: str | None, action: str, object_id: str, code: str) -> None:
        """Record a failed operation only after its business transaction rolls back."""
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.audit(connection, actor_id, action, object_id, "FAILURE", {"code": code})
            connection.commit()

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
                    result = operation(connection, actor)
                    self.audit(connection, actor.id, action, object_id, "SUCCESS", result)
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
            self.failure(actor_id, action, object_id, exc.code)
            raise
        except sqlite3.Error as exc:
            code = "CONSTRAINT_CONFLICT" if isinstance(exc, sqlite3.IntegrityError) else "DB_ERROR"
            self.failure(actor_id, action, object_id, code)
            raise MarketError(code, "数据库操作未生效，请刷新后重试", 503) from exc

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

    def logout(self, token: str) -> None:
        """Invalidate the current session rather than just hiding CLI state."""
        with closing(self.database.connect()) as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash=?", (token_digest(token),))

    def me(self, token: str) -> User:
        """Return current account identity to presentation adapters."""
        with closing(self.database.connect()) as connection:
            return self.identity(connection, token)
