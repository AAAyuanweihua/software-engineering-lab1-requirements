"""Reservation aggregate: occupancy, cancellation and two-party confirmation."""

from campus_market.catalog import Catalog
from campus_market.models import new_id, now, required


class Trade(Catalog):
    """All methods execute through Core.write, including moderation-driven closing."""

    def reservation(self, connection, reservation_id):
        """Join immutable product ownership into each authorization decision."""
        return self.one(
            connection,
            "SELECT r.*,p.seller_id FROM reservations r JOIN products p ON p.id=r.product_id "
            "WHERE r.id=?",
            (reservation_id,),
        )

    def reserve(self, token, product_id, version, key):
        """Create a price snapshot and occupancy in the same transaction."""

        def operation(connection, actor):
            product = self.product(connection, product_id)
            self.require(product["seller_id"] != actor.id, "OWN_PRODUCT", "不能预约自己的商品")
            self.require(product["version"] == version, "STALE_VERSION", "商品信息已变化，请刷新")
            seller = self.one(
                connection, "SELECT enabled FROM users WHERE id=?", (product["seller_id"],)
            )
            self.require(
                product["status"] == "PUBLISHED"
                and not product["restricted"]
                and seller["enabled"],
                "STATE_CONFLICT",
                "商品当前不可预约",
            )
            reservation_id = new_id()
            connection.execute(
                "INSERT INTO reservations(id,product_id,buyer_id,price_cents,status,created_at) "
                "VALUES (?,?,?,?,'PENDING',?)",
                (reservation_id, product_id, actor.id, product["price_cents"], now()),
            )
            self.checkpoint("reservation_inserted")
            self.set_product_state(connection, product_id, "RESERVED")
            return self.reservation(connection, reservation_id)

        return self.write(token, "RESERVE", product_id, {"version": version}, operation, key)

    def seller_decision(self, token, reservation_id, accept, key):
        """The product seller alone accepts/rejects a pending reservation."""

        def operation(connection, actor):
            reservation = self.reservation(connection, reservation_id)
            self.require(
                actor.id == reservation["seller_id"], "FORBIDDEN", "仅本商品卖家可处理", 403
            )
            self.require(reservation["status"] == "PENDING", "STATE_CONFLICT", "预约已处理")
            state = "CONFIRMED" if accept else "REJECTED"
            connection.execute(
                "UPDATE reservations SET status=? WHERE id=?", (state, reservation_id)
            )
            self.set_product_state(
                connection, reservation["product_id"], "CONFIRMED" if accept else "PUBLISHED"
            )
            return self.reservation(connection, reservation_id)

        return self.write(
            token, "SELLER_DECISION", reservation_id, {"accept": accept}, operation, key
        )

    def cancel_pending(self, token, reservation_id, key):
        """Before acceptance the buyer may release occupancy unilaterally."""

        def operation(connection, actor):
            reservation = self.reservation(connection, reservation_id)
            self.require(actor.id == reservation["buyer_id"], "FORBIDDEN", "仅本人买家可取消", 403)
            self.require(
                reservation["status"] == "PENDING", "STATE_CONFLICT", "卖家确认后须申请取消"
            )
            connection.execute(
                "UPDATE reservations SET status='CANCELLED' WHERE id=?", (reservation_id,)
            )
            self.checkpoint("reservation_cancelled")
            self.set_product_state(connection, reservation["product_id"], "PUBLISHED")
            return self.reservation(connection, reservation_id)

        return self.write(token, "CANCEL_PENDING", reservation_id, {}, operation, key)

    def request_cancellation(self, token, reservation_id, reason, key):
        """A pending cancellation blocks delivery confirmation, including single-party progress."""
        reason = required(reason, "取消原因", 500)

        def operation(connection, actor):
            reservation = self.reservation(connection, reservation_id)
            self.require(actor.id == reservation["buyer_id"], "FORBIDDEN", "仅买家可申请取消", 403)
            self.require(reservation["status"] == "CONFIRMED", "STATE_CONFLICT", "当前不可申请取消")
            cancellation_id = new_id()
            connection.execute(
                "INSERT INTO cancellations VALUES (?,?,?,'PENDING',?,NULL)",
                (cancellation_id, reservation_id, reason, now()),
            )
            connection.execute(
                "UPDATE reservations SET status='CANCEL_PENDING' WHERE id=?", (reservation_id,)
            )
            return {"id": cancellation_id, "reservation_id": reservation_id, "status": "PENDING"}

        return self.write(
            token, "REQUEST_CANCEL", reservation_id, {"reason": reason}, operation, key
        )

    def decide_cancellation(self, token, cancellation_id, decision, key):
        """Rejection/withdrawal start a new delivery-confirmation epoch."""
        self.require(
            decision in ("APPROVED", "REJECTED", "WITHDRAWN"), "INVALID_INPUT", "处理结果无效", 422
        )

        def operation(connection, actor):
            cancellation = self.one(
                connection, "SELECT * FROM cancellations WHERE id=?", (cancellation_id,)
            )
            reservation = self.reservation(connection, cancellation["reservation_id"])
            expected = (
                reservation["buyer_id"] if decision == "WITHDRAWN" else reservation["seller_id"]
            )
            self.require(actor.id == expected, "FORBIDDEN", "无权处理该取消申请", 403)
            self.require(
                cancellation["status"] == "PENDING" and reservation["status"] == "CANCEL_PENDING",
                "STATE_CONFLICT",
                "申请已处理",
            )
            connection.execute(
                "UPDATE cancellations SET status=?,decided_at=? WHERE id=?",
                (decision, now(), cancellation_id),
            )
            if decision == "APPROVED":
                connection.execute(
                    "UPDATE reservations SET status='CANCELLED' WHERE id=?", (reservation["id"],)
                )
                self.checkpoint("reservation_cancelled")
                self.set_product_state(connection, reservation["product_id"], "PUBLISHED")
            else:
                connection.execute(
                    "UPDATE reservations SET status='CONFIRMED',buyer_confirmed=0,"
                    "seller_confirmed=0,epoch=epoch+1 WHERE id=?",
                    (reservation["id"],),
                )
            return self.reservation(connection, reservation["id"])

        return self.write(
            token, "DECIDE_CANCEL", cancellation_id, {"decision": decision}, operation, key
        )

    def confirm(self, token, reservation_id, epoch, key):
        """Only the actual buyer/seller confirms, and only in the current epoch."""

        def operation(connection, actor):
            reservation = self.reservation(connection, reservation_id)
            self.require(
                actor.id in (reservation["buyer_id"], reservation["seller_id"]),
                "FORBIDDEN",
                "不能代交易双方确认",
                403,
            )
            self.require(reservation["epoch"] == epoch, "STALE_EPOCH", "确认已重置，请刷新重新确认")
            self.require(
                reservation["status"] == "CONFIRMED", "STATE_CONFLICT", "当前交易不能完成确认"
            )
            column = (
                "buyer_confirmed" if actor.id == reservation["buyer_id"] else "seller_confirmed"
            )
            # Column is a fixed internal choice, never caller-supplied SQL.
            connection.execute(f"UPDATE reservations SET {column}=1 WHERE id=?", (reservation_id,))
            updated = self.reservation(connection, reservation_id)
            if updated["buyer_confirmed"] and updated["seller_confirmed"]:
                connection.execute(
                    "UPDATE reservations SET status='COMPLETED',completed_at=? WHERE id=?",
                    (now(), reservation_id),
                )
                self.set_product_state(connection, reservation["product_id"], "COMPLETED")
            self.checkpoint("confirmation_updated")
            return self.reservation(connection, reservation_id)

        return self.write(token, "CONFIRM", reservation_id, {"epoch": epoch}, operation, key)

    def _close_active(self, connection, product_id):
        """Close occupancy and pending cancellation together, preserving both histories."""
        connection.execute(
            "UPDATE cancellations SET status='CLOSED',decided_at=? WHERE status='PENDING' "
            "AND reservation_id IN (SELECT id FROM reservations WHERE product_id=?)",
            (now(), product_id),
        )
        connection.execute(
            "UPDATE reservations SET status='CLOSED' WHERE product_id=? "
            "AND status IN ('PENDING','CONFIRMED','CANCEL_PENDING')",
            (product_id,),
        )

    def restrict(self, token, product_id, reason):
        """Moderation and completion share the same database writer serialization."""
        reason = required(reason, "违规原因", 500)

        def operation(connection, _actor):
            product = self.product(connection, product_id)
            if product["status"] != "COMPLETED":
                self._close_active(connection, product_id)
                self.set_product_state(connection, product_id, "OFFLINE")
            connection.execute(
                "UPDATE products SET restricted=1,version=version+1 WHERE id=?", (product_id,)
            )
            return {"product": self.product(connection, product_id), "reason": reason}

        return self.write(token, "RESTRICT", product_id, {"reason": reason}, operation, admin=True)

    def unrestrict(self, token, product_id, reason):
        """Removing a restriction does not publish or revive a completed product."""
        reason = required(reason, "解除原因", 500)

        def operation(connection, _actor):
            product = self.product(connection, product_id)
            self.require(bool(product["restricted"]), "STATE_CONFLICT", "商品未被限制")
            connection.execute(
                "UPDATE products SET restricted=0,version=version+1 WHERE id=?", (product_id,)
            )
            return {"product": self.product(connection, product_id), "reason": reason}

        return self.write(
            token, "UNRESTRICT", product_id, {"reason": reason}, operation, admin=True
        )

    def disable_user(self, token, user_id, reason):
        """Suspend new activity and revoke sessions; outstanding history is retained."""
        reason = required(reason, "停用原因", 500)

        def operation(connection, actor):
            self.require(actor.id != user_id, "INVALID_INPUT", "不能停用当前管理员自身", 422)
            self.one(connection, "SELECT id FROM users WHERE id=?", (user_id,))
            connection.execute("UPDATE users SET enabled=0 WHERE id=?", (user_id,))
            connection.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            return {"id": user_id, "enabled": False, "reason": reason}

        return self.write(token, "DISABLE", user_id, {"reason": reason}, operation, admin=True)

    def close_disabled_trade(self, token, reservation_id, reason):
        """An administrator may release an unfinished trade with a suspended participant."""
        reason = required(reason, "关闭原因", 500)

        def operation(connection, _actor):
            reservation = self.reservation(connection, reservation_id)
            participants = connection.execute(
                "SELECT enabled FROM users WHERE id IN (?,?)",
                (reservation["buyer_id"], reservation["seller_id"]),
            ).fetchall()
            self.require(
                any(not row["enabled"] for row in participants),
                "STATE_CONFLICT",
                "仅处理含停用参与方的交易",
            )
            self.require(
                reservation["status"] in ("PENDING", "CONFIRMED", "CANCEL_PENDING"),
                "STATE_CONFLICT",
                "只可关闭未完成交易",
            )
            self._close_active(connection, reservation["product_id"])
            product = self.product(connection, reservation["product_id"])
            seller = self.one(
                connection, "SELECT enabled FROM users WHERE id=?", (reservation["seller_id"],)
            )
            state = "PUBLISHED" if seller["enabled"] and not product["restricted"] else "OFFLINE"
            self.set_product_state(connection, product["id"], state)
            return {"reservation": self.reservation(connection, reservation_id), "reason": reason}

        return self.write(
            token, "CLOSE_DISABLED", reservation_id, {"reason": reason}, operation, admin=True
        )
