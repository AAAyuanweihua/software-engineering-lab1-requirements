"""Regressions derived from R01-R07/N01, separate from the preserved baseline tests."""

import io
import json
import sqlite3
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stdout
from unittest.mock import patch

import test_market as fixtures

from campus_market.cli import Shell
from campus_market.models import MarketError, new_id
from campus_market.queries import Market


class ReviewRegressions(unittest.TestCase):
    """Reuse fixtures, not inheritance: the original 22 tests are run only once."""

    setUp = fixtures.MarketTests.setUp
    tearDown = fixtures.MarketTests.tearDown
    assert_invariants = fixtures.MarketTests.assert_invariants
    product = fixtures.MarketTests.product
    reserve = fixtures.MarketTests.reserve
    accepted = fixtures.MarketTests.accepted
    assert_error = fixtures.MarketTests.assert_error

    def counts(self):
        with closing(self.market.database.connect()) as connection:
            return {
                table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("reservations", "reviews", "receipts", "audit_logs")
            }

    def test_r01_review_requires_real_boolean_without_side_effects(self):
        draft = self.product(publish=False)
        pending = self.market.submit_product(self.seller, draft["id"])
        baseline = self.counts()
        for value in ("false", "true", 0, 1, None, [], {}):
            with self.subTest(value=value):
                self.assert_error(
                    "INVALID_INPUT",
                    self.market.review_product,
                    self.admin,
                    draft["id"],
                    pending["version"],
                    value,
                )
                self.assertEqual(self.counts(), baseline)
                self.assertEqual(
                    self.market.get_product(draft["id"], self.admin)["status"], "REVIEWING"
                )
        rejected = self.market.review_product(
            self.admin, draft["id"], pending["version"], False, "请补充描述"
        )
        self.assertEqual(rejected["status"], "DRAFT")

    def test_r01_seller_decision_requires_real_boolean(self):
        reservation = self.reserve()
        baseline = self.counts()
        for value in ("false", "true", 0, 1, None, [], {}):
            self.assert_error(
                "INVALID_INPUT",
                self.market.seller_decision,
                self.seller,
                reservation["id"],
                value,
                new_id(),
            )
            self.assertEqual(self.counts(), baseline)
        rejected = self.market.seller_decision(self.seller, reservation["id"], False, new_id())
        self.assertEqual(rejected["status"], "REJECTED")

    def test_r02_all_query_parameter_types_and_sqlite_offset_bounds(self):
        cases = [{"keyword": x} for x in (1, None, [], {}, True)]
        cases += [
            {"category": []},
            {"scope": {}},
            {"order": []},
            {"status": 1},
            {"page": True},
            {"page": 0},
            {"page": 2**100},
            {"page": 1.5},
            {"minimum": []},
            {"maximum": False},
            {"token": None},
        ]
        for params in cases:
            with self.subTest(params=params):
                self.assert_error("INVALID_INPUT", self.market.list_products, **params)

    def test_r02_shell_continues_after_bad_json_type_and_logs_out(self):
        shell = Shell(self.market)
        shell.token = self.buyer
        stream = io.StringIO()
        with (
            patch(
                "builtins.input",
                side_effect=['list_products {"keyword":1}', "list_products {}", "quit"],
            ) as input_,
            redirect_stdout(stream),
        ):
            shell.run()
        self.assertEqual(input_.call_count, 3)
        self.assertIn('"error": "INVALID_INPUT"', stream.getvalue())
        self.assertIn('"items": []', stream.getvalue())
        self.assertEqual(shell.token, "")
        self.assert_error("UNAUTHENTICATED", self.market.me, self.buyer)

    def test_r02_finally_cleans_session_on_unexpected_adapter_error(self):
        shell = Shell(self.market)
        shell.token = self.buyer
        with (
            patch("builtins.input", return_value="help"),
            patch.object(shell, "execute", side_effect=RuntimeError("adapter failure")),
            redirect_stdout(io.StringIO()),
        ):
            with self.assertRaises(RuntimeError):
                shell.run()
        self.assertEqual(shell.token, "")
        self.assert_error("UNAUTHENTICATED", self.market.me, self.buyer)

    def test_r03_persistent_lock_returns_business_error_and_truthful_audit_status(self):
        product = self.product()
        database = self.market.database
        original = database.connect

        def fast_connect():
            connection = original()
            connection.execute("PRAGMA busy_timeout=1")
            return connection

        with closing(original()) as locker:
            locker.execute("BEGIN IMMEDIATE")
            started = time.monotonic()
            with (
                patch.object(database, "connect", side_effect=fast_connect),
                self.assertLogs("campus_market.core", level="ERROR") as diagnostics,
            ):
                with self.assertRaises(MarketError) as caught:
                    self.market.take_down(self.seller, product["id"])
            elapsed = time.monotonic() - started
            locker.rollback()
        self.assertEqual(caught.exception.code, "DB_ERROR")
        self.assertEqual(caught.exception.audit_status, "unavailable")
        self.assertLess(elapsed, 2.0)
        self.assertTrue(caught.exception.diagnostic_id)
        self.assertIn("AUDIT_UNAVAILABLE", "\n".join(diagnostics.output))
        self.assertEqual(self.market.get_product(product["id"])["status"], "PUBLISHED")
        self.assertEqual(self.market.logs(self.admin, action="TAKE_DOWN"), [])

    def test_r03_lock_released_during_audit_persists_failure(self):
        product = self.product()
        database = self.market.database
        original = database.connect
        audit_started = threading.Event()
        calls = []

        def fast_connect():
            connection = original()
            connection.execute("PRAGMA busy_timeout=1")
            calls.append(1)
            if len(calls) >= 2:
                audit_started.set()
            return connection

        def invoke():
            try:
                self.market.take_down(self.seller, product["id"])
            except MarketError as error:
                return error
            self.fail("Expected original busy write to fail")

        with closing(original()) as locker, ThreadPoolExecutor(max_workers=1) as pool:
            locker.execute("BEGIN IMMEDIATE")
            with patch.object(database, "connect", side_effect=fast_connect):
                future = pool.submit(invoke)
                self.assertTrue(audit_started.wait(3))
                locker.rollback()
                error = future.result(timeout=3)
        self.assertEqual(error.code, "DB_ERROR")
        self.assertEqual(error.audit_status, "persisted")
        logs = self.market.logs(self.admin, action="TAKE_DOWN")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["outcome"], "FAILURE")
        self.assertEqual(json.loads(logs[0]["detail"])["diagnostic_id"], error.diagnostic_id)

    def test_r04_market_sqlite_and_runtime_faults_have_rollback_and_safe_audit(self):
        product = self.product()
        marker = "password_or_sql_should_not_be_logged"
        cases = [
            (MarketError("TEST_FAULT", marker), "TEST_FAULT"),
            (sqlite3.OperationalError(marker), "DB_ERROR"),
            (RuntimeError(marker), "INTERNAL_ERROR"),
        ]
        with patch("campus_market.core.LOGGER.error") as diagnostic:
            for exception, code in cases:

                def fail(name, exception=exception):
                    if name == "reservation_inserted":
                        raise exception

                service = Market(self.market.database, fault=fail)
                with self.assertRaises(MarketError) as caught:
                    service.reserve(self.buyer, product["id"], product["version"], new_id())
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.audit_status, "persisted")
        with closing(self.market.database.connect()) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM reservations").fetchone()[0], 0
            )
            self.assertEqual(connection.execute("SELECT count(*) FROM receipts").fetchone()[0], 0)
        logs = self.market.logs(self.admin, action="RESERVE")
        self.assertEqual([x["outcome"] for x in logs], ["FAILURE"] * 3)
        self.assertNotIn(marker, str(logs))
        self.assertNotIn(marker, str(diagnostic.call_args_list))

    def interleaved_query(self, callback, method, *args, **kwargs):
        original = self.market.database.connect
        fired = []

        class CursorProxy:
            def __init__(self, cursor):
                self.cursor = cursor

            def fetchone(self):
                value = self.cursor.fetchone()
                if not fired:
                    fired.append(True)
                    callback()
                return value

        class ConnectionProxy:
            def __init__(self):
                self.connection = original()

            def execute(self, sql, params=()):
                cursor = self.connection.execute(sql, params)
                if sql.startswith("SELECT count(*) FROM products p"):
                    return CursorProxy(cursor)
                return cursor

            def close(self):
                self.connection.close()

        class ReaderDatabase:
            def connect(self):
                return ConnectionProxy()

        result = getattr(Market(ReaderDatabase()), method)(*args, **kwargs)
        self.assertTrue(fired)
        return result

    def test_r05_takedown_between_count_and_rows_keeps_one_response_snapshot(self):
        product = self.product()
        result = self.interleaved_query(
            lambda: self.market.take_down(self.seller, product["id"]), "list_products"
        )
        self.assertEqual((result["total"], len(result["items"])), (1, 1))
        self.assertEqual(self.market.list_products()["total"], 0)

    def test_r05_publication_between_count_and_rows_keeps_one_response_snapshot(self):
        draft = self.product(publish=False)
        pending = self.market.submit_product(self.seller, draft["id"])
        result = self.interleaved_query(
            lambda: self.market.review_product(self.admin, draft["id"], pending["version"], True),
            "list_products",
        )
        self.assertEqual((result["total"], result["items"]), (0, []))
        self.assertEqual(self.market.list_products()["total"], 1)

    def test_r05_equal_price_order_is_stable_but_separate_pages_use_new_snapshots(self):
        products = [self.product(price="35.00") for _ in range(21)]
        ordered_ids = sorted(product["id"] for product in products)
        first = self.market.list_products(order="price_asc")
        second = self.market.list_products(order="price_asc", page=2)
        self.assertEqual([row["id"] for row in first["items"]], ordered_ids[:20])
        self.assertEqual([row["id"] for row in second["items"]], ordered_ids[20:])
        self.market.take_down(self.seller, ordered_ids[0])
        # Each response is coherent; separate calls deliberately do not share a snapshot.
        updated_second = self.market.list_products(order="price_asc", page=2)
        updated_first = self.market.list_products(order="price_asc")
        self.assertEqual((updated_second["total"], updated_second["items"]), (20, []))
        self.assertEqual([row["id"] for row in updated_first["items"]], ordered_ids[1:])

    def test_r06_restrict_includes_closed_reservation_and_cancellation(self):
        reservation = self.accepted()
        self.market.confirm(self.buyer, reservation["id"], 1, new_id())
        cancellation = self.market.request_cancellation(
            self.buyer, reservation["id"], "取消", new_id()
        )
        self.market.restrict(self.admin, reservation["product_id"], "违规关闭")
        detail = json.loads(self.market.logs(self.admin, action="RESTRICT")[-1]["detail"])
        for side, state, application in [
            ("before", "CANCEL_PENDING", "PENDING"),
            ("after", "CLOSED", "CLOSED"),
        ]:
            linked = detail[side]
            row = linked["related_reservations"][reservation["id"]]
            self.assertEqual(row["status"], state)
            self.assertEqual(row["buyer_confirmed"], 1)
            self.assertEqual(
                linked["related_cancellations"][cancellation["id"]]["status"], application
            )
        self.assertEqual(detail["reason"], "违规关闭")
        self.assertNotIn("description", json.dumps(detail))
        self.assertNotIn("password", json.dumps(detail))

    def test_r06_admin_disabled_close_preserves_aggregate_transition_audit(self):
        reservation = self.accepted()
        cancellation = self.market.request_cancellation(
            self.buyer, reservation["id"], "取消", new_id()
        )
        self.market.disable_user(self.admin, self.ids["buyer"], "停用")
        self.market.close_disabled_trade(self.admin, reservation["id"], "有因关闭")
        detail = json.loads(self.market.logs(self.admin, action="CLOSE_DISABLED")[-1]["detail"])
        self.assertEqual(
            detail["before"]["related_reservations"][reservation["id"]]["status"], "CANCEL_PENDING"
        )
        self.assertEqual(
            detail["after"]["related_reservations"][reservation["id"]]["status"], "CLOSED"
        )
        self.assertEqual(
            detail["after"]["related_cancellations"][cancellation["id"]]["status"], "CLOSED"
        )

    def test_r06_cancel_rejection_and_withdrawal_audit_marks_and_epoch(self):
        for decision, actor in [("REJECTED", self.seller), ("WITHDRAWN", self.buyer)]:
            reservation = self.accepted()
            self.market.confirm(self.buyer, reservation["id"], 1, new_id())
            cancellation = self.market.request_cancellation(
                self.buyer, reservation["id"], "申请", new_id()
            )
            self.market.decide_cancellation(actor, cancellation["id"], decision, new_id())
            detail = json.loads(self.market.logs(self.admin, action="DECIDE_CANCEL")[-1]["detail"])
            before = detail["before"]["related_reservations"][reservation["id"]]
            after = detail["after"]["related_reservations"][reservation["id"]]
            self.assertEqual((before["epoch"], before["buyer_confirmed"]), (1, 1))
            self.assertEqual(
                (after["epoch"], after["buyer_confirmed"], after["seller_confirmed"]), (2, 0, 0)
            )
            self.assertEqual(
                detail["after"]["related_cancellations"][cancellation["id"]]["status"], decision
            )

    def test_r07_all_required_receipt_operations_reject_invalid_keys_before_write(self):
        calls = [
            (self.market.reserve, {"product_id": "x", "version": 1}),
            (self.market.seller_decision, {"reservation_id": "x", "accept": True}),
            (self.market.cancel_pending, {"reservation_id": "x"}),
            (self.market.request_cancellation, {"reservation_id": "x", "reason": "取消"}),
            (self.market.decide_cancellation, {"cancellation_id": "x", "decision": "REJECTED"}),
            (self.market.confirm, {"reservation_id": "x", "epoch": 1}),
        ]
        baseline = self.counts()
        for operation, params in calls:
            for key in (None, "", "  ", 0, False, [], {}, "x" * 101):
                self.assert_error("INVALID_INPUT", operation, self.buyer, **params, key=key)
            self.assert_error("INVALID_INPUT", operation, self.buyer, **params)
        self.assertEqual(self.counts(), baseline)

    def test_r07_valid_receipt_replay_and_cross_action_collision(self):
        product = self.product()
        key = new_id()
        reservation = self.market.reserve(self.buyer, product["id"], product["version"], key)
        self.assertEqual(
            reservation, self.market.reserve(self.buyer, product["id"], product["version"], key)
        )
        self.assert_error(
            "KEY_CONFLICT", self.market.cancel_pending, self.buyer, reservation["id"], key
        )
        self.market.cancel_pending(self.buyer, reservation["id"], new_id())

    def test_n01_trimmed_category_is_validated_and_persisted_consistently(self):
        draft = self.market.create_product(self.seller, "教材", " 教材 ", "10", "描述", "book.jpg")
        self.assertEqual(draft["category"], "教材")
        changed = self.market.edit_product(
            self.seller, draft["id"], "教材", " 数码 ", "20", "描述", "book.jpg"
        )
        self.assertEqual(changed["category"], "数码")
