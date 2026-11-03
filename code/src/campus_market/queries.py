"""Permission-aware reads and exact, half-open-window reporting."""

from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone

from campus_market.models import MarketError, ProductStatus, price_cents
from campus_market.trade import Trade

BEIJING = timezone(timedelta(hours=8))


def utc_boundary(value: str) -> str:
    """Interpret naive dates/times as Beijing, then normalize to stored UTC precision."""
    try:
        instant = datetime.fromisoformat(value)
    except ValueError as exc:
        raise MarketError("INVALID_TIME", "日期须为ISO格式，例如2026-11-01", 422) from exc
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=BEIJING)
    return instant.astimezone(UTC).isoformat(timespec="microseconds")


class Market(Trade):
    """Public facade for presentation and tests, with fresh authorization per read."""

    def list_products(
        self,
        token="",
        scope="public",
        keyword="",
        category="",
        minimum=None,
        maximum=None,
        status=None,
        order="newest",
        page=1,
    ):
        """Combine conditions, apply visibility first, and return deterministic pages of 20."""
        self.require(isinstance(page, int) and page >= 1, "INVALID_PAGE", "页码须为正整数", 422)
        sorts = {
            "newest": "p.published_at DESC,p.id ASC",
            "price_asc": "p.price_cents ASC,p.id ASC",
            "price_desc": "p.price_cents DESC,p.id ASC",
        }
        self.require(order in sorts, "INVALID_SORT", "排序方式无效", 422)
        conditions, args = [], []
        with closing(self.database.connect()) as connection:
            actor = self.identity(connection, token) if token else None
            if scope == "public":
                conditions.extend(["p.status='PUBLISHED'", "p.restricted=0", "u.enabled=1"])
            elif scope == "own":
                self.require(actor is not None, "UNAUTHENTICATED", "请先登录", 401)
                conditions.append("p.seller_id=?")
                args.append(actor.id)
            elif scope == "admin":
                self.require(
                    actor is not None and actor.admin, "FORBIDDEN", "仅管理员可查全部", 403
                )
            else:
                raise MarketError("INVALID_SCOPE", "查询范围无效", 422)
            if status:
                self.require(status in ProductStatus, "INVALID_STATUS", "商品状态无效", 422)
                conditions.append("p.status=?")
                args.append(status)
            if keyword:
                escaped = keyword.replace("!", "!!").replace("%", "!%").replace("_", "!_")
                conditions.append("(p.title LIKE ? ESCAPE '!' OR p.description LIKE ? ESCAPE '!')")
                args.extend([f"%{escaped}%", f"%{escaped}%"])
            if category:
                conditions.append("p.category=?")
                args.append(category)
            low = 0 if minimum in (None, "0", "0.00") else price_cents(minimum)
            high = 9_999_999 if maximum is None else (0 if maximum == "0" else price_cents(maximum))
            self.require(low <= high, "INVALID_RANGE", "最低价格不能高于最高价格", 422)
            conditions.append("p.price_cents BETWEEN ? AND ?")
            args.extend([low, high])
            where = " AND ".join(conditions) if conditions else "1=1"
            base = " FROM products p JOIN users u ON u.id=p.seller_id WHERE " + where
            total = connection.execute("SELECT count(*)" + base, args).fetchone()[0]
            rows = connection.execute(
                "SELECT p.*" + base + " ORDER BY " + sorts[order] + " LIMIT 20 OFFSET ?",
                [*args, (page - 1) * 20],
            ).fetchall()
            return {
                "items": [dict(row) for row in rows],
                "total": total,
                "page": page,
                "page_size": 20,
            }

    def get_product(self, product_id, token=""):
        """Do not leak another seller's non-public draft by direct identifier."""
        with closing(self.database.connect()) as connection:
            product = self.product(connection, product_id)
            actor = self.identity(connection, token) if token else None
            public = product["status"] == "PUBLISHED" and not product["restricted"]
            seller = self.one(
                connection, "SELECT enabled FROM users WHERE id=?", (product["seller_id"],)
            )
            public = public and seller["enabled"]
            own = actor and (actor.id == product["seller_id"] or actor.admin)
            self.require(bool(public or own), "NOT_FOUND", "商品不存在或不可访问", 404)
            return product

    def get_reservation(self, token, reservation_id):
        """A participant can read their own history; administrators can govern it."""
        with closing(self.database.connect()) as connection:
            actor = self.identity(connection, token)
            reservation = self.reservation(connection, reservation_id)
            self.require(
                actor.admin or actor.id in (reservation["buyer_id"], reservation["seller_id"]),
                "FORBIDDEN",
                "无权查看他人私人预约",
                403,
            )
            reservation["cancellations"] = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM cancellations WHERE reservation_id=? ORDER BY created_at,id",
                    (reservation_id,),
                )
            ]
            return reservation

    def list_reservations(self, token):
        """Return only the current account's buying and selling business records."""
        with closing(self.database.connect()) as connection:
            actor = self.identity(connection, token)
            rows = connection.execute(
                "SELECT r.*,p.title,p.seller_id FROM reservations r "
                "JOIN products p ON p.id=r.product_id WHERE r.buyer_id=? OR p.seller_id=? "
                "ORDER BY r.created_at DESC,r.id",
                (actor.id, actor.id),
            )
            return [dict(row) for row in rows]

    def seller_stats(self, token, start, end):
        """Count completed deals once, by completion time and frozen reservation price."""
        start_utc, end_utc = utc_boundary(start), utc_boundary(end)
        self.require(start_utc < end_utc, "INVALID_RANGE", "结束时间必须晚于开始时间", 422)
        with closing(self.database.connect()) as connection:
            actor = self.identity(connection, token)
            row = connection.execute(
                "SELECT count(*) AS completed_count,coalesce(sum(r.price_cents),0) AS total_cents "
                "FROM reservations r JOIN products p ON p.id=r.product_id "
                "WHERE p.seller_id=? AND r.status='COMPLETED' AND r.completed_at>=? "
                "AND r.completed_at<?",
                (actor.id, start_utc, end_utc),
            ).fetchone()
            return {**dict(row), "start_utc": start_utc, "end_utc_exclusive": end_utc}

    def category_stats(self, token, status=None):
        """Count products, not joined reservation history; default includes all states."""
        with closing(self.database.connect()) as connection:
            actor = self.identity(connection, token)
            self.require(actor.admin, "FORBIDDEN", "仅管理员可查询分类统计", 403)
            if status:
                self.require(status in ProductStatus, "INVALID_STATUS", "商品状态无效", 422)
            rows = connection.execute(
                "SELECT category,count(*) AS count FROM products "
                + ("WHERE status=? " if status else "")
                + "GROUP BY category ORDER BY category",
                (status,) if status else (),
            )
            return [dict(row) for row in rows]

    def logs(self, token, start=None, end=None, actor_id=None, action=None):
        """Administrator-only read API; no application method updates/deletes logs."""
        conditions, args = [], []
        if start:
            conditions.append("created_at>=?")
            args.append(utc_boundary(start))
        if end:
            conditions.append("created_at<?")
            args.append(utc_boundary(end))
        for column, value in [("actor_id", actor_id), ("action", action)]:
            if value:
                conditions.append(column + "=?")
                args.append(value)
        with closing(self.database.connect()) as connection:
            actor = self.identity(connection, token)
            self.require(actor.admin, "FORBIDDEN", "仅管理员可查操作日志", 403)
            where = " WHERE " + " AND ".join(conditions) if conditions else ""
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM audit_logs" + where + " ORDER BY created_at,id",
                    args,
                )
            ]

    def users(self, token):
        """Management listing excludes password hashes and session credentials."""
        with closing(self.database.connect()) as connection:
            actor = self.identity(connection, token)
            self.require(actor.admin, "FORBIDDEN", "仅管理员可查用户列表", 403)
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT id,account,enabled,admin FROM users ORDER BY account"
                )
            ]
