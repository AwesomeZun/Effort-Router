#!/usr/bin/env python3
"""P1 프로젝트 탐색 HTTP 통합테스트 — H1~H16 (bundle-v3 §P1 RED).

하위 프로세스 서버(--workspace + --root fixture)로 API 계약·Host·Sec-Fetch-Site·
심볼릭 제외·bundle 오라클을 전수한다. test_dashboard_server.py와 동일 관습:
subprocess 기동, READY 1행, 트리 불변 스냅샷, http.client path-as-is.
심볼릭 링크 생성 실패 환경은 링크 의존 케이스를 skip한다.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / 'scripts/dashboard_server.py'

READY_TIMEOUT_SEC = 10.0
EXIT_TIMEOUT_SEC = 10.0
HTTP_TIMEOUT_SEC = 10.0

HOME_PHASE = 'home-marker-phase'
OTHER_PHASE = 'other-marker-phase'
HOME_MARKER = 'home alpha 번들 마커'
OTHER_MARKER = 'other alpha 번들 마커'

ALPHA_STATE = {
    'task': 'alpha-done', 'tier': 'M', 'phase': 'home-phase',
    'claims': [], 'bundle': 'docs/task-id/alpha-done/bundle.md',
}
OTHER_ALPHA_STATE = {
    'task': 'alpha-done', 'tier': 'M', 'phase': 'other-phase',
    'claims': [], 'bundle': 'docs/task-id/alpha-done/bundle.md',
}
MALFORMED_TEXT = '{ 이것은 json이 아니다'

HOME_BUNDLE = '# home alpha\n\n## 1. 원 요구 원문\n\n> ' + HOME_MARKER + '\n'
OTHER_BUNDLE = '# other alpha\n\n> ' + OTHER_MARKER + '\n'


def oracle_state(task: str, bundle: str) -> dict:
    return {'task': task, 'tier': 'S', 'phase': 'oracle', 'claims': [],
            'bundle': bundle}


LINKED_STATE = oracle_state('linked-state', 'docs/task-id/linked-state/state.json')
LINKED_BUNDLE_STATE = oracle_state(
    'linked-bundle', 'docs/task-id/linked-bundle/bundle.md')

ORACLE_STANDARD = oracle_state('oracle-standard',
                               'docs/task-id/oracle-standard/bundle.md')
ORACLE_ALTLOC = oracle_state('oracle-altloc', 'notes/bundle.md')
ORACLE_ABS = oracle_state('oracle-abs', '/etc/hostname')
ORACLE_UPDOWN = oracle_state('oracle-updown', '../x')
ORACLE_TRAV = oracle_state('oracle-trav', 'a/../../x')
ORACLE_LINKESC = oracle_state('oracle-linkesc', 'linkdir/secret.md')

_FIXTURE_TMP = tempfile.TemporaryDirectory()
FIXTURE_BASE = Path(_FIXTURE_TMP.name)
WORKSPACE = FIXTURE_BASE / 'ws'
HOME_PROJECT = WORKSPACE / 'home-proj'
OTHER_PROJECT = WORKSPACE / 'other-proj'
OUTSIDE = FIXTURE_BASE / 'outside'
LINKS_OK = True  # setUpModule이 확정
_SHARED = {}  # proc·base_url·snap0


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def write_task(project: Path, folder: str, state: dict,
               bundle_text: 'str | None' = None) -> None:
    write_text(project / f'docs/task-id/{folder}/state.json',
               json.dumps(state, ensure_ascii=False))
    if bundle_text is not None:
        write_text(project / f'docs/task-id/{folder}/bundle.md', bundle_text)


def try_symlink(target: Path, link: Path) -> bool:
    try:
        if link.is_symlink() or link.exists():
            link.unlink()
        os.symlink(str(target), str(link))
        return link.is_symlink()
    except OSError:
        return False


def make_fixture() -> None:
    """bundle-v3 §P1 H fixture — ws 하위 2 프로젝트 + 제외 대상 + 오라클."""
    global LINKS_OK
    OUTSIDE.mkdir(parents=True)
    write_text(OUTSIDE / 'secret.md', '외부 비밀')
    write_text(OUTSIDE / 'external-state.json', '{"task": "linked"}')
    write_text(OUTSIDE / 'external-bundle.md', '# 외부 번들')
    # home 프로젝트
    write_task(HOME_PROJECT, 'alpha-done', ALPHA_STATE, HOME_BUNDLE)
    write_text(HOME_PROJECT / 'docs/task-id/gamma-malformed/state.json',
               MALFORMED_TEXT)
    write_task(HOME_PROJECT, 'oracle-standard', ORACLE_STANDARD, '# 표준 번들')
    write_task(HOME_PROJECT, 'oracle-altloc', ORACLE_ALTLOC)
    write_text(HOME_PROJECT / 'notes/bundle.md', '# 프로젝트 내 다른 위치')
    for state in (ORACLE_ABS, ORACLE_UPDOWN, ORACLE_TRAV):
        write_task(HOME_PROJECT, state['task'], state)
    write_task(HOME_PROJECT, 'oracle-linkesc', ORACLE_LINKESC)
    write_task(HOME_PROJECT, 'linked-state', LINKED_STATE)
    write_task(HOME_PROJECT, 'linked-bundle', LINKED_BUNDLE_STATE, 'X')
    # other 프로젝트 — 같은 폴더명 alpha-done, 다른 마커(H5)
    write_task(OTHER_PROJECT, 'alpha-done', OTHER_ALPHA_STATE, OTHER_BUNDLE)
    # 제외 대상
    (WORKSPACE / 'no-docs').mkdir(parents=True)
    hidden = WORKSPACE / '.hidden-proj'
    (hidden / 'docs/task-id').mkdir(parents=True)
    (WORKSPACE / 'file.txt').write_text('not a project', encoding='utf-8')
    # 링크 fixture — ws 밖 파일 가리킴
    LINKS_OK = True
    LINKS_OK &= try_symlink(OUTSIDE, WORKSPACE / 'link-proj')
    LINKS_OK &= try_symlink(OUTSIDE, HOME_PROJECT / 'linkdir')
    LINKS_OK &= try_symlink(OUTSIDE / 'external-state.json',
                            HOME_PROJECT / 'docs/task-id/linked-state/state.json')
    LINKS_OK &= try_symlink(OUTSIDE / 'external-bundle.md',
                            HOME_PROJECT / 'docs/task-id/linked-bundle/bundle.md')
    LINKS_OK &= try_symlink(OUTSIDE,
                            HOME_PROJECT / 'docs/task-id/linked-task')


def tree_snapshot(root: Path) -> list:
    entries = []
    for path in sorted(root.rglob('*')):
        rel = str(path.relative_to(root))
        if path.is_file():
            entries.append((rel, hashlib.sha256(path.read_bytes()).hexdigest()))
        else:
            entries.append((rel, '<dir>'))
    return entries


def spawn_server(workspace: 'Path | None', root: Path, port: int = 0) -> subprocess.Popen:
    cmd = [sys.executable, str(SERVER)]
    if workspace is not None:
        cmd += ['--workspace', str(workspace)]
    cmd += ['--root', str(root), '--port', str(port)]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)


def read_ready(proc: subprocess.Popen) -> str:
    line = proc.stdout.readline().strip()
    if not line.startswith('READY '):
        raise AssertionError(f'READY 라인 계약 위반: {line!r}')
    return line[len('READY '):]


def spawn_and_ready(workspace: 'Path | None', root: Path) -> 'tuple[subprocess.Popen, str]':
    proc = spawn_server(workspace, root)
    try:
        base_url = read_ready(proc)
    except Exception:
        proc.kill()
        proc.wait()
        raise
    return proc, base_url


def stop_process(proc: subprocess.Popen, close_streams: bool = True) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=EXIT_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    if close_streams:
        for stream in (proc.stdout, proc.stderr):
            if stream and not stream.closed:
                stream.close()


def http_request(base_url: str, path: str, method: str = 'GET',
                 headers: 'dict | None' = None, send_host: bool = True,
                 host_value: 'str | None' = None):
    """원시 요청 — Host 헤더 제어(D7 누락 검증)·Sec-Fetch-Site 주입(D8) 지원."""
    parts = urlsplit(base_url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port,
                                      timeout=HTTP_TIMEOUT_SEC)
    try:
        conn.putrequest(method, path, skip_host=True)
        if send_host:
            conn.putheader('Host', host_value or f'{parts.hostname}:{parts.port}')
        for key, value in (headers or {}).items():
            conn.putheader(key, value)
        conn.endheaders()
        resp = conn.getresponse()
        body = resp.read().decode('utf-8', errors='replace')
        return resp.status, dict(resp.getheaders()), body
    finally:
        conn.close()


def get_json(base_url: str, path: str) -> dict:
    status, _, body = http_request(base_url, path)
    assert status == 200, f'{path} 상태 {status}'
    return json.loads(body)


def setUpModule():
    make_fixture()
    _SHARED['snap0'] = tree_snapshot(WORKSPACE)
    try:
        _SHARED['proc'], _SHARED['base_url'] = spawn_and_ready(WORKSPACE, HOME_PROJECT)
    except Exception as exc:  # noqa: BLE001 — 기동 실패 자체가 RED 증거다
        _SHARED['proc'] = None
        _SHARED['base_url'] = None
        _SHARED['setup_error'] = str(exc)


def tearDownModule():
    stop_process(_SHARED.get('proc'))


class ProjectHttpTests(unittest.TestCase):
    """H1~H16 — 프로젝트 탐색 API·가드·심볼릭 제외 계약."""

    @property
    def base_url(self) -> str:
        base = _SHARED['base_url']
        if base is None:
            self.fail(f'공유 서버 미기동(서버 부재 = RED): {_SHARED.get("setup_error")}')
        return base

    def test_h01_projects_schema(self):
        """H1 — /api/projects 스키마·home·count·정렬."""
        status, headers, body = http_request(self.base_url, '/api/projects')
        self.assertEqual(status, 200)
        self.assertEqual(headers.get('Content-Type'),
                         'application/json; charset=utf-8')
        payload = json.loads(body)
        self.assertEqual(set(payload.keys()),
                         {'generated_at', 'home', 'count', 'projects'})
        self.assertEqual(payload['home'], 'home-proj')
        self.assertEqual(payload['count'], 2)
        self.assertEqual([p['id'] for p in payload['projects']],
                         ['home-proj', 'other-proj'])
        self.assertEqual([set(p.keys()) for p in payload['projects']],
                         [{'id'}, {'id'}])

    def test_h02_project_query_selects(self):
        """H2 — ?project=other-proj → other-proj 과업만."""
        payload = get_json(self.base_url, '/api/tasks?project=other-proj')
        self.assertEqual(payload['project'], 'other-proj')
        self.assertGreaterEqual(payload['count'], 1)
        for task in payload['tasks']:
            self.assertEqual(task['project'], 'other-proj')
        alpha = [t for t in payload['tasks'] if t['task'] == 'alpha-done']
        self.assertEqual(len(alpha), 1)
        self.assertEqual(alpha[0]['phase'], 'other-phase')

    def test_h03_no_query_is_home(self):
        """H3 — 쿼리 없음 = home + 최상위·과업별 project 키."""
        payload = get_json(self.base_url, '/api/tasks')
        self.assertEqual(payload['project'], 'home-proj')
        self.assertGreaterEqual(payload['count'], 1)
        for task in payload['tasks']:
            self.assertEqual(task['project'], 'home-proj')
        phases = {t['phase'] for t in payload['tasks']}
        self.assertIn('home-phase', phases)
        self.assertNotIn('other-phase', 'other-proj 과업이 home에 섞였다')

    def test_h04_project_query_404(self):
        """H4 — 미발견·숨김·링크·traversal·중복·빈 값·키만·필드 11개·대소문자 → 404."""
        eleven = '&'.join(f'f{i}=v' for i in range(10)) + '&project=home-proj'
        queries = [
            '?project=nope', '?project=.hidden-proj', '?project=no-docs',
            '?project=%2e%2e', '?project=home-proj&project=other-proj',
            '?project=', '?project', '?' + eleven, '?project=HOME-PROJ',
        ]
        if LINKS_OK:
            queries.append('?project=link-proj')
        for query in queries:
            status, _, body = http_request(self.base_url, '/api/tasks' + query)
            self.assertEqual(status, 404, f'{query} → {status}')
            self.assertEqual(body, 'not found', f'{query} 본문 반사')

    def test_h05_two_segment_bundle(self):
        """H5 — 2세그먼트 HOME/OTHER 구분 + 키 집합 정확 일치."""
        for project, marker in (('home-proj', HOME_MARKER),
                                ('other-proj', OTHER_MARKER)):
            path = f'/api/bundle/{project}/alpha-done'
            status, headers, body = http_request(self.base_url, path)
            self.assertEqual(status, 200, path)
            self.assertEqual(headers.get('Content-Type'),
                             'application/json; charset=utf-8')
            payload = json.loads(body)
            self.assertEqual(set(payload.keys()),
                             {'project', 'folder', 'markdown'})
            self.assertEqual(payload['project'], project)
            self.assertEqual(payload['folder'], 'alpha-done')
            self.assertIn(marker, payload['markdown'])

    def test_h06_one_segment_legacy(self):
        """H6 — 1세그먼트 레거시 = home, 키 {folder, markdown} 정확."""
        status, _, body = http_request(self.base_url, '/api/bundle/alpha-done')
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(set(payload.keys()), {'folder', 'markdown'})
        self.assertEqual(payload['folder'], 'alpha-done')
        self.assertIn(HOME_MARKER, payload['markdown'])

    def test_h07_two_segment_traversal(self):
        """H7 — 2세그먼트 트래버설·링크·대소문자 변형 11종 404 + 본문 고정."""
        attempts = [
            '/api/bundle/..%2fscripts/alpha-done',
            '/api/bundle/home-proj/%2e%2e',
            '/api/bundle/home-proj/%252e%252e',
            '/api/bundle/home-proj/alpha-done/x',
            '/api/bundle//alpha-done',
            '/api/bundle/home-proj/',
            '/api/bundle/home-proj/alpha%2fdone',
            '/api/bundle/other-proj/ALPHA-DONE',
            '/api/bundle/home-proj/nonexistent',
            '/api/bundle/nonexistent/alpha-done',
        ]
        if LINKS_OK:
            attempts += [
                '/api/bundle/home-proj/linked-state',
                '/api/bundle/home-proj/linked-bundle',
            ]
        for path in attempts:
            status, _, body = http_request(self.base_url, path)
            self.assertEqual(status, 404, f'{path} → {status}')
            self.assertEqual(body, 'not found', f'{path} 본문에 경로 반사')

    def test_h08_parse_error_isolation(self):
        """H8 — malformed parse_error에 워크스페이스 절대경로 없음."""
        payload = get_json(self.base_url, '/api/tasks?project=home-proj')
        gamma = [t for t in payload['tasks'] if t['task'] == 'gamma-malformed']
        self.assertEqual(len(gamma), 1)
        self.assertIsInstance(gamma[0]['parse_error'], str)
        self.assertGreater(len(gamma[0]['parse_error']), 0)
        self.assertNotIn(str(WORKSPACE), gamma[0]['parse_error'])
        self.assertNotIn(str(HOME_PROJECT), gamma[0]['parse_error'])

    def test_h09_post_projects_405(self):
        """H9 — POST /api/projects → 405 text/plain."""
        status, headers, body = http_request(self.base_url, '/api/projects',
                                             method='POST')
        self.assertEqual(status, 405)
        self.assertEqual(body, 'method not allowed')
        self.assertNotIn('<!DOCTYPE', body)

    def test_h10_tree_immutable(self):
        """H10 — 전 시나리오 후 트리 불변(읽기 전용)."""
        status, _, _ = http_request(self.base_url, '/api/tasks?project=home-proj')
        self.assertEqual(status, 200)
        self.assertEqual(tree_snapshot(WORKSPACE), _SHARED['snap0'])

    def test_h11_no_workspace_single_project(self):
        """H11 — --workspace 없으면 /api/projects count 1(home 단일)."""
        proc, base_url = spawn_and_ready(None, HOME_PROJECT)
        try:
            payload = get_json(base_url, '/api/projects')
            self.assertEqual(payload['count'], 1)
            self.assertEqual([p['id'] for p in payload['projects']],
                             ['home-proj'])
            tasks = get_json(base_url, '/api/tasks?project=home-proj')
            self.assertEqual(tasks['project'], 'home-proj')
        finally:
            stop_process(proc)

    def test_h12_host_guard(self):
        """H12 — Host evil·누락·포트 불일치 404, localhost:<포트> 200."""
        port = urlsplit(self.base_url).port
        status, _, _ = http_request(self.base_url, '/api/projects',
                                    host_value=f'evil.example:{port}')
        self.assertEqual(status, 404, 'evil Host 허용')
        status, _, _ = http_request(self.base_url, '/api/projects',
                                    send_host=False)
        self.assertEqual(status, 404, 'Host 누락 허용')
        status, _, _ = http_request(self.base_url, '/api/projects',
                                    host_value=f'127.0.0.1:{port + 1}')
        self.assertEqual(status, 404, '포트 불일치 허용')
        status, _, _ = http_request(self.base_url, '/api/projects',
                                    host_value=f'localhost:{port}')
        self.assertEqual(status, 200)

    def test_h13_sec_fetch_site_guard(self):
        """H13 — /api/* cross-site·same-site 404, same-origin·none 200, / 면제."""
        for value, expected in (('cross-site', 404), ('same-site', 404),
                                ('same-origin', 200), ('none', 200)):
            status, _, _ = http_request(
                self.base_url, '/api/projects',
                headers={'Sec-Fetch-Site': value})
            self.assertEqual(status, expected, f'api Sec-Fetch-Site {value}')
        status, _, _ = http_request(self.base_url, '/',
                                    headers={'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(status, 200, '정적 라우트는 면제돼야 한다')
        status, _, _ = http_request(self.base_url, '/api/bundle/home-proj/alpha-done',
                                    headers={'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(status, 404, '번들 API도 Sec-Fetch-Site 가드 대상이다')

    @unittest.skipUnless(os.name == 'posix', '심볼릭 링크 필요')
    def test_h14_linked_excluded(self):
        """H14 — 링크된 과업·state는 목록 제외, 링크된 bundle은 excerpt null·exists false."""
        if not LINKS_OK:
            self.skipTest('심볼릭 링크 생성 불가 환경')
        payload = get_json(self.base_url, '/api/tasks?project=home-proj')
        folders = {t['folder'] for t in payload['tasks']}
        self.assertNotIn('linked-task', folders, '과업 디렉터리 링크 포함')
        self.assertNotIn('linked-state', folders, 'state 링크 과업 포함')
        linked = [t for t in payload['tasks'] if t['folder'] == 'linked-bundle']
        self.assertEqual(len(linked), 1)
        self.assertIsNone(linked[0]['excerpt'], '링크 bundle excerpt가 null이 아니다')
        self.assertFalse(linked[0]['bundle_exists'], '링크 bundle exists가 false가 아니다')

    def test_h15_bundle_field_oracle(self):
        """H15 — bundle 필드 오라클: 표준형·내부 다른 위치 true, 절대경로·..·링크 탈출 false."""
        payload = get_json(self.base_url, '/api/tasks?project=home-proj')
        by_folder = {t['folder']: t for t in payload['tasks']}
        self.assertTrue(by_folder['oracle-standard']['bundle_exists'])
        self.assertTrue(by_folder['oracle-altloc']['bundle_exists'])
        for folder in ('oracle-abs', 'oracle-updown', 'oracle-trav'):
            self.assertFalse(by_folder[folder]['bundle_exists'], folder)
        if LINKS_OK:
            self.assertFalse(by_folder['oracle-linkesc']['bundle_exists'],
                             '내부 링크 디렉터리 탈출이 true다')

    def test_h16_workspace_missing_exit_2(self):
        """H16 — --workspace 미존재 경로 → exit 2 + stderr 사유."""
        proc = spawn_server(FIXTURE_BASE / 'no-such-ws', HOME_PROJECT)
        try:
            code = proc.wait(timeout=EXIT_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            self.fail('미존재 --workspace에서 서버가 기동해 버렸다')
        self.assertEqual(code, 2)
        stderr = proc.stderr.read()
        self.assertGreater(len(stderr.strip()), 0, 'stderr 사유 부재')


if __name__ == '__main__':
    unittest.main()
