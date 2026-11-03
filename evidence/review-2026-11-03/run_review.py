"""Read-only source review verification; all test databases live in this output folder."""
from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import secrets
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing, redirect_stdout
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

WORKSPACE = Path(__file__).resolve().parents[2]
CODE = WORKSPACE / '实验3_241220055_王陈钰' / 'code'
OUT = Path(__file__).resolve().parent
EVIDENCE = OUT / 'evidence'
EVIDENCE.mkdir(exist_ok=True)
sys.path.insert(0, str(CODE / 'src'))
from campus_market.database import Database
from campus_market.queries import Market
from campus_market.cli import Shell
from campus_market.models import MarketError, new_id


def main():
    sources = sorted((CODE / 'src' / 'campus_market').glob('*.py'))
    sources += sorted((CODE / 'tests').glob('*.py')) + sorted((CODE / 'tools').glob('*.py'))
    sources += [CODE / 'pyproject.toml']
    manifest = {
        'review_date': '2026-11-03',
        'started_at': datetime.now(timezone(timedelta(hours=8))).isoformat(),
        'python': sys.version, 'platform': platform.platform(), 'sqlite': sqlite3.sqlite_version,
        'files': [{'file': p.relative_to(CODE).as_posix(), 'lines': len(p.read_text(encoding='utf-8').splitlines()),
                   'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in sources],
    }
    env = dict(os.environ, PYTHONPATH=str(CODE / 'src'), PYTHONIOENCODING='utf-8',
               TEMP=str(OUT), TMP=str(OUT), PYTHONDONTWRITEBYTECODE='1')
    command = [sys.executable, '-X', 'utf8', '-m', 'unittest', 'discover', '-s', 'tests', '-v']
    result = subprocess.run(command, cwd=CODE, env=env, capture_output=True, encoding='utf-8', timeout=180)
    (EVIDENCE / 'baseline-tests.txt').write_text(result.stdout + result.stderr, encoding='utf-8')
    manifest['baseline_exit_code'] = result.returncode
    print('BASELINE', result.returncode, flush=True)
    ruff = WORKSPACE / '实验3_241220055_王陈钰_工作材料' / 'lint-tools' / 'bin' / 'ruff.exe'
    for name, args in [('lint', ['check']), ('format', ['format', '--check'])]:
        check = subprocess.run([str(ruff), *args, '--no-cache', 'src', 'tests', 'tools'], cwd=CODE,
                               capture_output=True, encoding='utf-8', timeout=30)
        (EVIDENCE / f'{name}.txt').write_text(check.stdout + check.stderr, encoding='utf-8')
        manifest[name + '_exit_code'] = check.returncode
        print(name.upper(), check.returncode, flush=True)

    probes = []
    with tempfile.TemporaryDirectory(dir=OUT, prefix='probe-') as temp:
        db = Database(Path(temp) / 'review.db')
        market = Market(db)
        password = secrets.token_urlsafe(24)
        tokens = {}
        for name in ('admin', 'seller', 'buyer'):
            market.register(name, password, bootstrap=name == 'admin')
            tokens[name] = market.login(name, password)
        admin, seller, buyer = (tokens[x] for x in ('admin', 'seller', 'buyer'))

        def product(publish=True):
            draft = market.create_product(seller, '教材', '教材', '35.00', '完整教材', 'book.jpg')
            if not publish:
                return draft
            pending = market.submit_product(seller, draft['id'])
            return market.review_product(admin, draft['id'], pending['version'], True)

        draft = product(False)
        pending = market.submit_product(seller, draft['id'])
        wrong = market.review_product(admin, draft['id'], pending['version'], 'false')
        reservation = market.reserve(buyer, wrong['id'], wrong['version'], new_id())
        accepted = market.seller_decision(seller, reservation['id'], 'false', new_id())
        assert wrong['status'] == 'PUBLISHED' and accepted['status'] == 'CONFIRMED'
        probes.append({'id': 'R01', 'input': {'approve': 'false', 'accept': 'false'},
                       'observed': {'review_status': wrong['status'], 'reservation_status': accepted['status']}})

        shell = Shell(market)
        shell.token = buyer
        error_type = None
        try:
            with patch('builtins.input', side_effect=['list_products {"keyword":1}', 'quit']), redirect_stdout(io.StringIO()):
                shell.run()
        except Exception as exc:
            error_type = type(exc).__name__
        assert error_type == 'AttributeError'
        probes.append({'id': 'R02', 'input': 'list_products {"keyword":1}',
                       'observed': {'unhandled_exception': error_type, 'shell_returned_normally': False}})

        original_connect = db.connect
        def fast_connect():
            connection = original_connect()
            connection.execute('PRAGMA busy_timeout=1')
            return connection
        locker = original_connect()
        locker.execute('BEGIN IMMEDIATE')
        lock_error = None
        try:
            with patch.object(db, 'connect', side_effect=fast_connect):
                market.take_down(seller, wrong['id'])
        except Exception as exc:
            lock_error = type(exc).__name__
        finally:
            locker.rollback()
            locker.close()
        assert lock_error == 'OperationalError'
        probes.append({'id': 'R03', 'method': 'hold an independent writer; reduce busy timeout to 1ms only for probe',
                       'observed': {'unhandled_exception': lock_error, 'expected_service_error': 'MarketError'}})

        candidate = product()
        before_logs = len(market.logs(admin, action='RESERVE'))
        def fault(name):
            if name == 'reservation_inserted':
                raise RuntimeError('review failure seam')
        runtime_error = None
        try:
            Market(db, fault=fault).reserve(buyer, candidate['id'], candidate['version'], new_id())
        except Exception as exc:
            runtime_error = type(exc).__name__
        with closing(db.connect()) as connection:
            rows = connection.execute('SELECT count(*) FROM reservations WHERE product_id=?', (candidate['id'],)).fetchone()[0]
        delta = len(market.logs(admin, action='RESERVE')) - before_logs
        assert runtime_error == 'RuntimeError' and rows == 0 and delta == 0
        probes.append({'id': 'R04', 'input': 'fault seam raises RuntimeError after reservation insert',
                       'observed': {'exception': runtime_error, 'reservation_rows': rows, 'new_failure_audits': delta}})

        class CursorProxy:
            def __init__(self, cursor):
                self.cursor = cursor
            def fetchone(self):
                row = self.cursor.fetchone()
                market.take_down(seller, candidate['id'])
                return row
        class ConnectionProxy:
            def __init__(self):
                self.connection = original_connect()
            def execute(self, sql, params=()):
                cursor = self.connection.execute(sql, params)
                return CursorProxy(cursor) if sql.startswith('SELECT count(*) FROM products p') else cursor
            def close(self):
                self.connection.close()
        class ReadDatabase:
            def connect(self):
                return ConnectionProxy()
        query = Market(ReadDatabase()).list_products()
        assert query['total'] == len(query['items']) + 1
        probes.append({'id': 'R05', 'method': 'interleave committed take_down between count and item SELECTs using separate connections',
                       'observed': {'total': query['total'], 'items_count': len(query['items']), 'page': 1}})

        cancellation = market.request_cancellation(buyer, accepted['id'], '取消', new_id())
        market.restrict(admin, accepted['product_id'], '审查处置')
        log = market.logs(admin, action='RESTRICT')[-1]
        detail = json.loads(log['detail'])
        current = market.get_reservation(admin, accepted['id'])
        assert current['status'] == 'CLOSED' and current['cancellations'][0]['status'] == 'CLOSED'
        assert set(detail['before']) == {'products'} and set(detail['after']) == {'products'}
        probes.append({'id': 'R06', 'input': 'restrict a CANCEL_PENDING trade',
                       'observed': {'reservation_status': current['status'], 'cancellation_status': current['cancellations'][0]['status'],
                                    'before_snapshot_keys': list(detail['before']), 'after_snapshot_keys': list(detail['after']),
                                    'related_reservation_id_in_log': accepted['id'] in log['detail'],
                                    'related_cancellation_id_in_log': cancellation['id'] in log['detail']}})

        candidate = product()
        before_receipts = None
        with closing(db.connect()) as connection:
            before_receipts = connection.execute('SELECT count(*) FROM receipts').fetchone()[0]
        first = market.reserve(buyer, candidate['id'], candidate['version'], None)
        retry_error = None
        try:
            market.reserve(buyer, candidate['id'], candidate['version'], None)
        except MarketError as exc:
            retry_error = exc.code
        with closing(db.connect()) as connection:
            receipt_delta = connection.execute('SELECT count(*) FROM receipts').fetchone()[0] - before_receipts
        assert first['status'] == 'PENDING' and retry_error == 'STALE_VERSION' and receipt_delta == 0
        probes.append({'id': 'R07', 'input': {'key': None}, 'observed': {'first_status': first['status'],
                       'retry_error': retry_error, 'new_receipts': receipt_delta}})

        category_error = None
        try:
            market.create_product(seller, '教材', ' 教材 ', '35.00', '描述', 'book.jpg')
        except MarketError as exc:
            category_error = exc.code
        assert category_error == 'NOT_FOUND'
        probes.append({'id': 'N01', 'input': {'category': ' 教材 '}, 'observed': {'error': category_error}})

    manifest['probes'] = probes
    manifest['finished_at'] = datetime.now(timezone(timedelta(hours=8))).isoformat()
    manifest['source_unchanged'] = all(hashlib.sha256((CODE / f['file']).read_bytes()).hexdigest() == f['sha256'] for f in manifest['files'])
    mirror = WORKSPACE / '实验3_241220055_王陈钰_工作材料' / '远程核验' / 'code'
    manifest['mirror_identical'] = all((mirror / f['file']).exists() and (mirror / f['file']).read_bytes() == (CODE / f['file']).read_bytes() for f in manifest['files'])
    (EVIDENCE / 'review-results.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: manifest[k] for k in ('baseline_exit_code', 'lint_exit_code', 'format_exit_code', 'source_unchanged', 'mirror_identical')}, ensure_ascii=False))
    print('PROBES', len(probes), 'verified', flush=True)


if __name__ == '__main__':
    main()
