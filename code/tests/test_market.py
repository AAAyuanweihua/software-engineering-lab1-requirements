"""Behavioral tests for cross-record invariants, authorization and actual contention."""

import multiprocessing
import secrets
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from campus_market.cli import Shell
from campus_market.database import Database
from campus_market.models import MarketError, new_id, price_cents
from campus_market.queries import Market, utc_boundary


def competing_action(path, token, operation, params, barrier, queue):
    """Race two distinct business actions across independent writer processes."""
    market = Market(Database(path))
    barrier.wait(timeout=15)
    try:
        result = getattr(market, operation)(token=token, **params)
        queue.put(("success", operation, result))
    except MarketError as exc:
        queue.put(("error", operation, exc.code))


def competing_reservation(path, token, product_id, version, barrier, queue):
    """Run in a separate process, each with an independent SQLite connection."""
    market = Market(Database(path))
    barrier.wait(timeout=15)
    try:
        result = market.reserve(token, product_id, version, new_id())
        queue.put(("success", result["id"]))
    except MarketError as exc:
        queue.put(("error", exc.code))


class MarketTests(unittest.TestCase):
    """Every test also checks the product/reservation aggregate after completion."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "test.db"
        self.market = Market(Database(self.path))
        self.credentials = {}
        self.ids = {}
        self.password = secrets.token_urlsafe(18)
        for name in ("admin", "seller", "buyer", "other"):
            self.ids[name] = self.market.register(name, self.password, bootstrap=name == "admin")
            self.credentials[name] = self.market.login(name, self.password)
        self.admin, self.seller, self.buyer, self.other = (
            self.credentials[x] for x in ("admin", "seller", "buyer", "other")
        )

    def tearDown(self):
        try:
            self.assert_invariants()
        finally:
            self.temp.cleanup()

    def assert_invariants(self):
        with closing(self.market.database.connect()) as connection:
            for product in connection.execute("SELECT * FROM products"):
                rows = connection.execute(
                    "SELECT * FROM reservations WHERE product_id=? "
                    "AND status IN ('PENDING','CONFIRMED','CANCEL_PENDING')",
                    (product["id"],),
                ).fetchall()
                self.assertLessEqual(len(rows), 1)
                if product["status"] == "RESERVED":
                    self.assertEqual([r["status"] for r in rows], ["PENDING"])
                elif product["status"] == "CONFIRMED":
                    self.assertEqual(len(rows), 1)
                    self.assertIn(rows[0]["status"], ("CONFIRMED", "CANCEL_PENDING"))
                else:
                    self.assertEqual(rows, [])
                if product["status"] == "COMPLETED":
                    completed = connection.execute(
                        "SELECT * FROM reservations WHERE product_id=? AND status='COMPLETED'",
                        (product["id"],),
                    ).fetchall()
                    self.assertEqual(len(completed), 1)
                    self.assertTrue(completed[0]["buyer_confirmed"])
                    self.assertTrue(completed[0]["seller_confirmed"])
                    self.assertIsNotNone(completed[0]["completed_at"])

    def product(self, price="35.00", publish=True):
        draft = self.market.create_product(
            self.seller, "线代教材", "教材", price, "九成新，无缺页", "book.jpg"
        )
        if not publish:
            return draft
        pending = self.market.submit_product(self.seller, draft["id"])
        return self.market.review_product(self.admin, draft["id"], pending["version"], True)

    def reserve(self, product=None):
        product = product or self.product()
        return self.market.reserve(self.buyer, product["id"], product["version"], new_id())

    def accepted(self):
        reservation = self.reserve()
        return self.market.seller_decision(self.seller, reservation["id"], True, new_id())

    def assert_error(self, code, callable_, *args, **kwargs):
        with self.assertRaises(MarketError) as caught:
            callable_(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_money_boundaries_and_no_rounding(self):
        for value in ("0", "-1", "NaN", "Infinity", "text", "1.001", "100000.00"):
            with self.subTest(value=value):
                self.assert_error("INVALID_PRICE", price_cents, value)
        self.assertEqual(price_cents("0.01"), 1)
        self.assertEqual(price_cents("99999.99"), 9_999_999)

    def test_registration_and_credentials(self):
        self.assert_error("ACCOUNT_EXISTS", self.market.register, "buyer", self.password)
        self.assert_error("ADMIN_EXISTS", self.market.register, "evil", self.password, True)
        self.assert_error("BAD_CREDENTIALS", self.market.login, "buyer", "wrong-password")
        self.assert_error("FORBIDDEN", self.market.create_admin, self.buyer, "evil", self.password)
        account = self.market.create_admin(self.admin, "second-admin", self.password)
        self.assertTrue(account["admin"])
        with closing(self.market.database.connect()) as connection:
            dump = "\n".join(connection.iterdump())
            self.assertNotIn(self.password, dump)
            self.assertNotIn(self.buyer, dump)
        self.market.logout(self.buyer)
        self.assert_error("UNAUTHENTICATED", self.market.me, self.buyer)

    def test_private_records_and_ownership(self):
        product = self.product(publish=False)
        self.assert_error("NOT_FOUND", self.market.get_product, product["id"], self.buyer)
        self.assert_error("FORBIDDEN", self.market.submit_product, self.buyer, product["id"])
        self.assert_error("FORBIDDEN", self.market.list_products, self.buyer, "admin")
        self.assertEqual(self.market.list_products(scope="public", status="DRAFT")["total"], 0)
        reservation = self.reserve()
        self.assert_error("FORBIDDEN", self.market.get_reservation, self.other, reservation["id"])
        self.assert_error(
            "FORBIDDEN", self.market.confirm, self.admin, reservation["id"], 1, new_id()
        )

    def test_review_rejection_and_resubmission(self):
        draft = self.product(publish=False)
        pending = self.market.submit_product(self.seller, draft["id"])
        self.assert_error(
            "INVALID_INPUT",
            self.market.review_product,
            self.admin,
            draft["id"],
            pending["version"],
            False,
        )
        result = self.market.review_product(
            self.admin, draft["id"], pending["version"], False, "补说明"
        )
        self.assertEqual(result["status"], "DRAFT")
        self.market.submit_product(self.seller, draft["id"])
        self.assert_error(
            "STALE_VERSION",
            self.market.review_product,
            self.admin,
            draft["id"],
            pending["version"],
            True,
        )

    def test_price_change_invalidates_old_page(self):
        product = self.product()
        edited = self.market.edit_product(
            self.seller, product["id"], "线代教材", "教材", "40.00", "九成新", "book.jpg"
        )
        self.assertEqual(edited["status"], "REVIEWING")
        published = self.market.review_product(self.admin, product["id"], edited["version"], True)
        self.assert_error(
            "STALE_VERSION",
            self.market.reserve,
            self.buyer,
            product["id"],
            product["version"],
            new_id(),
        )
        reservation = self.reserve(published)
        self.assertEqual(reservation["price_cents"], 4000)

    def test_duplicate_request_and_key_misuse(self):
        product = self.product()
        key = new_id()
        first = self.market.reserve(self.buyer, product["id"], product["version"], key)
        self.assertEqual(
            first, self.market.reserve(self.buyer, product["id"], product["version"], key)
        )
        self.assert_error(
            "KEY_CONFLICT",
            self.market.reserve,
            self.buyer,
            product["id"],
            product["version"] + 1,
            key,
        )
        self.assert_error(
            "OWN_PRODUCT",
            self.market.reserve,
            self.seller,
            product["id"],
            product["version"],
            new_id(),
        )
        self.assert_error("STATE_CONFLICT", self.market.take_down, self.seller, product["id"])

    def test_cross_process_last_item_competition(self):
        product = self.product()
        ctx = multiprocessing.get_context("spawn")
        barrier, queue = ctx.Barrier(2), ctx.Queue()
        processes = [
            ctx.Process(
                target=competing_reservation,
                args=(str(self.path), token, product["id"], product["version"], barrier, queue),
            )
            for token in (self.buyer, self.other)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=25)
            if process.is_alive():
                process.terminate()
                process.join()
                self.fail("competing process timed out")
            self.assertEqual(process.exitcode, 0)
        results = [queue.get(timeout=3) for _ in processes]
        queue.close()
        self.assertEqual(sum(x[0] == "success" for x in results), 1, results)
        self.assertEqual(sum(x[0] == "error" for x in results), 1, results)

    def test_cancel_before_accept_and_snapshot_history(self):
        product = self.product()
        reservation = self.reserve(product)
        self.market.cancel_pending(self.buyer, reservation["id"], new_id())
        self.assert_error(
            "STATE_CONFLICT",
            self.market.seller_decision,
            self.seller,
            reservation["id"],
            True,
            new_id(),
        )
        edited = self.market.edit_product(
            self.seller, product["id"], "教材", "教材", "99.00", "修改价格", "book.jpg"
        )
        self.market.review_product(self.admin, product["id"], edited["version"], True)
        self.assertEqual(
            self.market.get_reservation(self.buyer, reservation["id"])["price_cents"], 3500
        )

    def test_reject_reservation_restores_publication(self):
        reservation = self.reserve()
        self.market.seller_decision(self.seller, reservation["id"], False, new_id())
        self.assertEqual(self.market.get_product(reservation["product_id"])["status"], "PUBLISHED")

    def test_cancellation_blocks_completion_and_resets_epoch(self):
        reservation = self.accepted()
        self.market.confirm(self.buyer, reservation["id"], 1, new_id())
        cancellation = self.market.request_cancellation(
            self.buyer, reservation["id"], "改主意", new_id()
        )
        self.assert_error(
            "STATE_CONFLICT", self.market.confirm, self.seller, reservation["id"], 1, new_id()
        )
        restored = self.market.decide_cancellation(
            self.seller, cancellation["id"], "REJECTED", new_id()
        )
        self.assertEqual(
            (restored["buyer_confirmed"], restored["seller_confirmed"], restored["epoch"]),
            (0, 0, 2),
        )
        self.assert_error(
            "STALE_EPOCH", self.market.confirm, self.buyer, reservation["id"], 1, new_id()
        )
        self.assert_error(
            "STATE_CONFLICT",
            self.market.decide_cancellation,
            self.buyer,
            cancellation["id"],
            "WITHDRAWN",
            new_id(),
        )

    def test_withdraw_and_approve_cancellation(self):
        reservation = self.accepted()
        first = self.market.request_cancellation(self.buyer, reservation["id"], "临时", new_id())
        reset = self.market.decide_cancellation(self.buyer, first["id"], "WITHDRAWN", new_id())
        self.assertEqual(reset["epoch"], 2)
        second = self.market.request_cancellation(self.buyer, reservation["id"], "取消", new_id())
        self.market.decide_cancellation(self.seller, second["id"], "APPROVED", new_id())
        self.assertEqual(self.market.get_product(reservation["product_id"])["status"], "PUBLISHED")

    def test_both_confirmation_orders_and_completion_idempotency(self):
        for order in [(self.buyer, self.seller), (self.seller, self.buyer)]:
            reservation = self.accepted()
            first = self.market.confirm(order[0], reservation["id"], 1, new_id())
            self.assertEqual(first["status"], "CONFIRMED")
            key = new_id()
            final = self.market.confirm(order[1], reservation["id"], 1, key)
            self.assertEqual(final, self.market.confirm(order[1], reservation["id"], 1, key))
            self.assertEqual(final["status"], "COMPLETED")
            self.assert_error(
                "STATE_CONFLICT", self.market.confirm, order[1], reservation["id"], 1, new_id()
            )

    def test_moderation_before_and_after_completion(self):
        reservation = self.accepted()
        self.market.restrict(self.admin, reservation["product_id"], "违规")
        self.assert_error(
            "STATE_CONFLICT", self.market.confirm, self.buyer, reservation["id"], 1, new_id()
        )
        another = self.accepted()
        self.market.confirm(self.buyer, another["id"], 1, new_id())
        final = self.market.confirm(self.seller, another["id"], 1, new_id())
        product = self.market.restrict(self.admin, another["product_id"], "完成后违规")["product"]
        self.assertEqual(product["status"], "COMPLETED")
        self.assertEqual(
            self.market.get_reservation(self.buyer, another["id"])["completed_at"],
            final["completed_at"],
        )
        self.assert_error("STATE_CONFLICT", self.market.restore_draft, self.seller, product["id"])

    def test_restore_requires_unrestriction_and_reapproval(self):
        product = self.product()
        self.market.take_down(self.seller, product["id"])
        self.market.restore_draft(self.seller, product["id"])
        self.market.restrict(self.admin, product["id"], "违规")
        self.assert_error("STATE_CONFLICT", self.market.restore_draft, self.seller, product["id"])
        self.market.unrestrict(self.admin, product["id"], "已修正")
        result = self.market.restore_draft(self.seller, product["id"])
        self.assertEqual(result["status"], "DRAFT")
        self.assertEqual(self.market.list_products()["total"], 0)

    def test_disabled_participant_pending_cancel_is_closed_consistently(self):
        reservation = self.accepted()
        cancellation = self.market.request_cancellation(
            self.buyer, reservation["id"], "取消", new_id()
        )
        self.market.disable_user(self.admin, self.ids["buyer"], "违规账号")
        self.assert_error("UNAUTHENTICATED", self.market.me, self.buyer)
        self.market.close_disabled_trade(self.admin, reservation["id"], "停用善后")
        result = self.market.get_reservation(self.admin, reservation["id"])
        self.assertEqual(result["status"], "CLOSED")
        self.assertEqual(result["cancellations"][0]["id"], cancellation["id"])
        self.assertEqual(result["cancellations"][0]["status"], "CLOSED")

    def test_ten_faults_each_creation_and_cancellation(self):
        product = self.product()

        def injected(name):
            if name in ("reservation_inserted", "reservation_cancelled"):
                raise MarketError("INJECTED_FAILURE", "测试注入", 503)

        faulty = Market(self.market.database, fault=injected)
        for _ in range(10):
            self.assert_error(
                "INJECTED_FAILURE",
                faulty.reserve,
                self.buyer,
                product["id"],
                product["version"],
                new_id(),
            )
        with closing(self.market.database.connect()) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM reservations").fetchone()[0], 0
            )
            self.assertEqual(connection.execute("SELECT count(*) FROM receipts").fetchone()[0], 0)
        reservation = self.reserve(product)
        for _ in range(10):
            self.assert_error(
                "INJECTED_FAILURE", faulty.cancel_pending, self.buyer, reservation["id"], new_id()
            )
        self.assertEqual(
            self.market.get_reservation(self.buyer, reservation["id"])["status"], "PENDING"
        )
        failures = self.market.logs(self.admin, action="RESERVE")
        self.assertEqual(sum(x["outcome"] == "FAILURE" for x in failures), 10)

    def test_confirmation_before_commit_fault_rolls_back_flags(self):
        reservation = self.accepted()

        def injected(name):
            if name == "before_commit":
                raise MarketError("INJECTED_FAILURE", "提交前失败", 503)

        faulty = Market(self.market.database, fault=injected)
        self.assert_error(
            "INJECTED_FAILURE", faulty.confirm, self.buyer, reservation["id"], 1, new_id()
        )
        self.assertEqual(
            self.market.get_reservation(self.buyer, reservation["id"])["buyer_confirmed"], 0
        )

    def test_half_open_stats_and_beijing_conversion(self):
        reservation = self.accepted()
        self.market.confirm(self.buyer, reservation["id"], 1, new_id())
        self.market.confirm(self.seller, reservation["id"], 1, new_id())
        boundary = utc_boundary("2026-11-01")
        with closing(self.market.database.connect()) as connection:
            connection.execute(
                "UPDATE reservations SET completed_at=? WHERE id=?", (boundary, reservation["id"])
            )
        self.assertEqual(boundary, "2026-10-31T16:00:00.000000+00:00")
        included = self.market.seller_stats(self.seller, "2026-11-01", "2026-11-02")
        excluded = self.market.seller_stats(self.seller, "2026-10-31", "2026-11-01")
        self.assertEqual((included["completed_count"], included["total_cents"]), (1, 3500))
        self.assertEqual(excluded["completed_count"], 0)

    def test_queries_pagination_wildcards_and_categories(self):
        for i in range(21):
            self.product(price=str(i + 1))
        result = self.market.list_products(
            keyword="教材", category="教材", minimum="1", maximum="20"
        )
        self.assertEqual((result["total"], len(result["items"])), (20, 20))
        self.assertEqual(len(self.market.list_products(page=2)["items"]), 1)
        first = self.market.list_products(order="price_asc")["items"]
        second = self.market.list_products(order="price_asc", page=2)["items"]
        self.assertEqual(len({r["id"] for r in first + second}), 21)
        self.assertEqual(self.market.list_products(keyword="%_'")["total"], 0)
        self.assert_error("INVALID_RANGE", self.market.list_products, minimum="30", maximum="20")
        self.assertEqual(self.market.category_stats(self.admin)[0]["count"], 21)

    def test_audit_permission_filters_and_append_only(self):
        product = self.product()
        self.market.reserve(self.buyer, product["id"], product["version"], new_id())
        self.assert_error("FORBIDDEN", self.market.logs, self.buyer)
        rows = self.market.logs(self.admin, actor_id=self.ids["buyer"], action="RESERVE")
        self.assertEqual(len(rows), 1)
        with closing(self.market.database.connect()) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM audit_logs")

    def test_failed_cli_account_switch_clears_old_identity(self):
        shell = Shell(self.market)
        shell.token = self.buyer
        with patch("campus_market.cli.getpass", return_value="wrong-password"):
            self.assert_error("BAD_CREDENTIALS", shell.execute, "login other")
        self.assertEqual(shell.token, "")
        self.assert_error("UNAUTHENTICATED", self.market.me, self.buyer)

    def test_moderation_and_completion_real_process_race(self):
        reservation = self.accepted()
        self.market.confirm(self.buyer, reservation["id"], 1, new_id())
        ctx = multiprocessing.get_context("spawn")
        barrier, queue = ctx.Barrier(2), ctx.Queue()
        operations = [
            (
                self.seller,
                "confirm",
                {"reservation_id": reservation["id"], "epoch": 1, "key": new_id()},
            ),
            (
                self.admin,
                "restrict",
                {"product_id": reservation["product_id"], "reason": "竞争测试"},
            ),
        ]
        processes = [
            ctx.Process(
                target=competing_action, args=(str(self.path), token, name, params, barrier, queue)
            )
            for token, name, params in operations
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=25)
            if process.is_alive():
                process.terminate()
                process.join()
                self.fail("competing action timed out")
            self.assertEqual(process.exitcode, 0)
        results = [queue.get(timeout=3) for _ in processes]
        queue.close()
        product = self.market.get_product(reservation["product_id"], self.admin)
        current = self.market.get_reservation(self.admin, reservation["id"])
        self.assertTrue(product["restricted"])
        self.assertIn(
            (product["status"], current["status"]),
            [("COMPLETED", "COMPLETED"), ("OFFLINE", "CLOSED")],
        )
        self.assertTrue(any(row[0] == "success" and row[1] == "restrict" for row in results))


if __name__ == "__main__":
    unittest.main(verbosity=2)
