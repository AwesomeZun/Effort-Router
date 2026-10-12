#!/usr/bin/env python3
"""P2a·P2b 프론트엔드 계약 테스트 — F1·F5·F6(정적·node·라우트 표) + F2·F3·F4(배선).

F1·F6는 dashboard_server 정적 라우트 계약, F5는 node --test 래퍼(node 부재
환경은 skip), F2~F4는 app.js·index.html·신규 모듈의 정적 배선 단정이다.
"""
from __future__ import annotations

import http.client
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / 'scripts/dashboard_server.py'
ASSETS = ROOT / 'scripts/dashboard_assets'
ROUTE_TEST = ROOT / 'scripts/test_dashboard_route.mjs'

sys.path.insert(0, str(ROOT / 'scripts'))
import dashboard_server  # noqa: E402 — 정적 라우트 표(F6) 검증용

READY_TIMEOUT_SEC = 10.0
HTTP_TIMEOUT_SEC = 10.0

# 기존 8키(r37 계약) + P2a 신규 2키
LEGACY_STATIC_ROUTES = {
    '/', '/app.js', '/style.css', '/tokens.css', '/markdown.js',
    '/views/list.js', '/views/activity.js', '/views/detail.js',
}
NEW_STATIC_ROUTES = {'/route.js', '/views/projects.js'}


def spawn_and_ready():
    tmp = tempfile.TemporaryDirectory()
    proc = subprocess.Popen(
        [sys.executable, str(SERVER), '--root', tmp.name, '--port', '0'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        line = proc.stdout.readline().strip()
        if not line.startswith('READY '):
            raise AssertionError(f'READY 라인 계약 위반: {line!r}')
        return tmp, proc, line[len('READY '):]
    except Exception:
        proc.kill()
        proc.wait()
        tmp.cleanup()
        raise


def stop_process(tmp, proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    for stream in (proc.stdout, proc.stderr):
        if stream and not stream.closed:
            stream.close()
    tmp.cleanup()


def http_request(base_url: str, path: str):
    parts = urlsplit(base_url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port,
                                      timeout=HTTP_TIMEOUT_SEC)
    try:
        conn.request('GET', path)
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders())
    finally:
        conn.close()


class StaticRouteTests(unittest.TestCase):
    """F1·F6 — 정적 라우트 계약."""

    @classmethod
    def setUpClass(cls):
        cls._tmp, cls.proc, cls.base_url = spawn_and_ready()

    @classmethod
    def tearDownClass(cls):
        stop_process(cls._tmp, cls.proc)

    def test_f01_new_static_routes_served(self):
        """F1 — /route.js·/views/projects.js 200 + text/javascript."""
        for path in sorted(NEW_STATIC_ROUTES):
            status, headers = http_request(self.base_url, path)
            self.assertEqual(status, 200, path)
            self.assertEqual(headers.get('Content-Type'),
                             'text/javascript; charset=utf-8', path)

    def test_f06_static_route_table(self):
        """F6 — STATIC_ROUTES = 기존 8키 + 신규 2키(그 외 키 변경 금지)."""
        routes = set(dashboard_server.STATIC_ROUTES)
        self.assertEqual(routes, LEGACY_STATIC_ROUTES | NEW_STATIC_ROUTES)
        self.assertEqual(len(dashboard_server.STATIC_ROUTES), 10)


class FrontendWiringTests(unittest.TestCase):
    """F2·F3·F4 — 배선 정적 단정 + F5 node 래퍼."""

    @staticmethod
    def read(path: str) -> str:
        return (ASSETS / path).read_text(encoding='utf-8')

    def test_f02_app_js_fetch_literals(self):
        """F2 — app.js fetch 리터럴 3종·'#/task/'·hashchange 유지."""
        app = self.read('app.js')
        self.assertIn("fetch('/api/tasks'", app, '/api/tasks 페치 리터럴 부재')
        self.assertIn("fetch('/api/bundle/", app, '/api/bundle 페치 리터럴 부재')
        self.assertIn("fetch('/api/projects'", app, '/api/projects 페치 리터럴 부재')
        self.assertIn("'#/task/'", app, "'#/task/' 리터럴 부재")
        self.assertIn('hashchange', app, 'hashchange 리스너 부재')

    def test_f03_project_dom_wiring(self):
        """F3 — app.js getElementById 2종·index.html 두 id·하드코딩 제거·워드마크 유지."""
        app = self.read('app.js')
        self.assertIn("getElementById('project-nav')", app)
        self.assertIn("getElementById('workspace-name')", app)
        html = self.read('index.html')
        self.assertIn('id="project-nav"', html)
        self.assertIn('id="workspace-name"', html)
        self.assertNotIn('acc0mplish / Effort-Router', html,
                         '워크스페이스명 하드코딩 잔존')
        self.assertIn('EFFORT/ROUTER', html, '워드마크 유지 계약')
        self.assertIn('로컬 프로젝트', html, '워크스페이스 라벨 유지 계약')

    def test_f04_no_innerhtml_localstorage_scheme(self):
        """F4 — projects.js·route.js·app.js innerHTML·localStorage 0,
        projects.js·route.js 외부 스킴 0."""
        for path in ('views/projects.js', 'route.js', 'app.js'):
            text = self.read(path)
            self.assertNotIn('innerHTML', text, f'{path} innerHTML 사용')
            self.assertNotIn('localStorage', text, f'{path} localStorage 사용')
        for path in ('views/projects.js', 'route.js'):
            text = self.read(path)
            self.assertNotIn('http://', text, f'{path} 외부 스킴')
            self.assertNotIn('https://', text, f'{path} 외부 스킴')

    def test_f05_node_route_tests(self):
        """F5 — node --test test_dashboard_route.mjs exit 0(node 부재 시 skip)."""
        try:
            completed = subprocess.run(
                ['node', '--test', str(ROUTE_TEST)],
                capture_output=True, text=True, timeout=60.0)
        except FileNotFoundError:
            self.skipTest('node 미설치 환경')
        self.assertEqual(completed.returncode, 0,
                         f'node --test 실패:\n{completed.stdout}\n{completed.stderr}')


if __name__ == '__main__':
    unittest.main()
