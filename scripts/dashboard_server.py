#!/usr/bin/env python3
"""r37 과업 상태 HTML 대시보드 — stdlib 전용 읽기 전용 로컬 서버.

todo-flow(JakeB-5/todo-flow, MIT)의 로컬 웹 대시보드 스타일을 effort-router
과업 상태용으로 재구현한 것이다(코드 이식 아님). `<root>/docs/task-id/*/
state.json`을 요청 시점마다 재스캔해 /api/tasks로 노출하며, 어떤 파일도 쓰지
않는다(GET 전용 — state.json writer는 메인 세션 단일 계약).

보안 설계: 라우트는 고정 매핑 테이블(정적 8키 + /api/tasks + /api/projects)과
/api/bundle/<folder>(레거시 home)·/api/bundle/<project>/<folder>(2세그먼트)
뿐이다. do_GET 첫 줄에서 Host 허용 목록(127.0.0.1:<실포트>·localhost:<실포트>,
누락 포함 외 404 — DNS 리바인딩 방어)를 검사하고, /api/*는 Sec-Fetch-Site
(same-origin·none·부재만 허용, 정적 라우트 면제)를 검사한다. 프로젝트·과업·번들
경로 해석은 dashboard_projects 모듈의 순수 경로 검사(절대경로·`..` 거부) →
디렉터리 목록 exact match → nofollow lstat → 번들 API 한정 resolve 포함 관계로
위임되며 임의 경로 지정은 구조적으로 불가하다. 응답 본문에 요청 경로를
재사용하지 않는다(민감 경로 반사 금지).

사용:
    python3 scripts/dashboard_server.py [--port 5777] [--bind 127.0.0.1] \
        [--root 저장소루트] [--workspace 워크스페이스루트]

--workspace를 명시하면 그 바로 아래 프로젝트가 /api/projects와 ?project=로
노출된다(노출 확대 — 명시 시에만). 미지정 또는 --root만이면 home 단일.

stdout 계약: 기동 성공 시 정확히 1행 `READY http://<bind>:<port>`(flush) —
이후 요청 로그는 stderr 전용이다.
종료 코드: 정상 종료(SIGINT/SIGTERM) 0 · config 오류(바인드 실패 등) 2 +
stderr 사유.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

import dashboard_projects as dp

DEFAULT_PORT = 5777
DEFAULT_BIND = '127.0.0.1'
SAFE_BINDS = ('127.0.0.1', 'localhost')
EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
PARSE_ERROR_MAX = 200
CLAIM_BUCKETS = ('verified', 'pending', 'gap')
DOCS_DIR = 'docs/task-id'
SERVER_NAME = 'r37-dashboard/1.0'

ASSETS_DIR = Path(__file__).resolve().parent / 'dashboard_assets'

# 정적 자원 고정 매핑 — 유일한 경로→파일 해석점(트래버설 구조적 불가의 근거).
# 이 테이블에 없는 경로는 파일시스템과 무관하게 404다.
STATIC_ROUTES = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
    '/style.css': ('style.css', 'text/css; charset=utf-8'),
    '/tokens.css': ('tokens.css', 'text/css; charset=utf-8'),
    '/markdown.js': ('markdown.js', 'text/javascript; charset=utf-8'),
    '/views/list.js': ('views/list.js', 'text/javascript; charset=utf-8'),
    '/views/activity.js': ('views/activity.js', 'text/javascript; charset=utf-8'),
    '/views/detail.js': ('views/detail.js', 'text/javascript; charset=utf-8'),
}
API_ROUTE = '/api/tasks'
PROJECTS_API_ROUTE = '/api/projects'
BUNDLE_API_PREFIX = '/api/bundle/'
BODY_NOT_FOUND = 'not found'
BODY_METHOD_NOT_ALLOWED = 'method not allowed'

# excerpt 발췌 — 번들 §인터페이스 3규칙(헤딩 이후 blockquote → 문서 전체 폴백 → null)
EXCERPT_MAX = 140
EXCERPT_HEADING = re.compile(r'^#{1,6}\s.*원.?요구')
BLOCKQUOTE_PREFIX = '> '
BLOCKQUOTE_BARE = '>'


def pure_path(raw_path: str) -> str:
    """쿼리스트링을 무시한 순수 경로 — /api/tasks?x=1 은 /api/tasks와 동일."""
    return raw_path.split('?', 1)[0]


def utc_now_iso() -> str:
    """ISO8601 UTC 타임스탬프(초 단위)."""
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def normalize_round(raw) -> dict:
    """round 8키 방어 — 누락·비정형 입력을 {adversary, review} 2키로 정규화."""
    if not isinstance(raw, dict):
        return {'adversary': 0, 'review': 0}
    return {
        'adversary': raw.get('adversary', 0) if isinstance(raw.get('adversary'), int) else 0,
        'review': raw.get('review', 0) if isinstance(raw.get('review'), int) else 0,
    }


def count_claims(claims: list) -> dict:
    """status별 3버킷(verified/pending/gap) + unknown은 other(무시하지 않고 집계)."""
    counts = {bucket: 0 for bucket in CLAIM_BUCKETS}
    counts['other'] = 0
    for item in claims:
        status = item.get('status') if isinstance(item, dict) else None
        if status in counts:
            counts[status] += 1
        else:
            counts['other'] += 1
    return counts


def claim_details(claims: list) -> list:
    """claims 상세 — claim·status·evidence 3키만 추출(나머지 키는 유출하지 않는다)."""
    details = []
    for item in claims:
        if not isinstance(item, dict):
            continue
        details.append({
            'claim': item.get('claim'),
            'status': item.get('status'),
            'evidence': item.get('evidence'),
        })
    return details


def empty_task(folder: str, project: str, bundle, parse_error, mtime) -> dict:
    """스키마 기본값 — malformed 격리 항목에도 동일 형태를 유지한다."""
    return {
        'task': folder, 'folder': folder, 'project': project,
        'tier': None, 'phase': None,
        'round': {'adversary': 0, 'review': 0}, 'spawns': 0,
        'claims_total': 0, 'claims': {'verified': 0, 'pending': 0, 'gap': 0, 'other': 0},
        'claim_details': [], 'bundle': bundle, 'bundle_exists': False,
        'next': None, 'parse_error': parse_error,
        'excerpt': None, 'mtime': mtime,
    }


def first_blockquote_line(lines: list) -> 'str | None':
    """`^> ` 최초 매칭 행에서 접두사를 제거해 반환 — 부재 시 None."""
    for line in lines:
        if line.startswith(BLOCKQUOTE_PREFIX):
            return line[len(BLOCKQUOTE_PREFIX):]
        if line == BLOCKQUOTE_BARE:
            return ''
    return None


def extract_excerpt(task_dir: Path) -> 'str | None':
    """원 요구 발췌 추출(번들 §/api/tasks — best-effort, parse_error와 무관).

    규칙1: '원 요구' 헤딩 최초 매칭 이후 첫 blockquote 행 · 규칙2: 헤딩 미발견·
    헤딩 이후 부재 시 문서 첫 blockquote 행(폴백) · 규칙3: 발췌원 전무·번들
    파일 부재·읽기 오류 → None. 140자 절단.
    """
    bundle_path = task_dir / 'bundle.md'
    text = dp.read_text_nofollow(bundle_path)
    if text is None:
        return None
    lines = text.splitlines()
    heading_at = None
    for idx, line in enumerate(lines):
        if EXCERPT_HEADING.match(line):
            heading_at = idx
            break
    scope = lines[heading_at + 1:] if heading_at is not None else lines
    excerpt = first_blockquote_line(scope)
    if excerpt is None:
        excerpt = first_blockquote_line(lines)
    if excerpt is None:
        return None
    return excerpt[:EXCERPT_MAX]


def state_mtime_iso(state: Path) -> 'str | None':
    """state.json 파일 mtime — ISO8601 UTC. malformed 항목도 파일은 존재하므로 부여."""
    try:
        mtime = state.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(mtime, timezone.utc).isoformat(timespec='seconds')


def truncate(text: str) -> str:
    return text if len(text) <= PARSE_ERROR_MAX else text[:PARSE_ERROR_MAX]


def sanitize_parse_error(exc: Exception, state: Path, root: Path) -> str:
    """parse_error 서명화 — 절대경로 유출 차단(④리뷰 LOW): 예외 타입명 + 경로 문자열을
    파일명·루트 마커로 치환한 뒤 절단. 응답에 호스트 파일시스템 경로를 노출하지 않는다."""
    message = str(exc).replace(str(state), state.name).replace(str(root), '.')
    return truncate(f'{type(exc).__name__}: {message}')


def load_task(entry: Path, project_root: Path, project_id: str,
              project_resolved: Path, docs_ok: bool) -> 'dict | None':
    """state.json 1건을 표시 항목으로 변환 — 파싱 실패는 parse_error로 격리.

    state.json이 링크이거나 읽을 수 없으면 None(목록 제외 — D9). folder 키 =
    과업 폴더명(④리뷰 LOW: 동명 task 엣지에서 행 식별 키로 사용 — task 값은
    state.json 기재값이라 중복 가능, 폴더명은 폴더별 유일).
    """
    state = entry / 'state.json'
    mtime = state_mtime_iso(state)
    raw_text = dp.read_text_nofollow(state)
    if raw_text is None:
        return None
    try:
        raw = json.loads(raw_text)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return empty_task(entry.name, project_id, None,
                          sanitize_parse_error(exc, state, project_root), mtime)
    if not isinstance(raw, dict):
        return empty_task(entry.name, project_id, None,
                          'state.json 루트가 객체가 아니다', mtime)
    bundle = raw.get('bundle')
    claims = raw.get('claims') if isinstance(raw.get('claims'), list) else []
    bundle_ok = dp.bundle_exists(bundle, project_root, project_resolved, docs_ok)
    return {
        'task': raw.get('task') if isinstance(raw.get('task'), str) else entry.name,
        'folder': entry.name,
        'project': project_id,
        'tier': raw.get('tier'),
        'phase': raw.get('phase'),
        'round': normalize_round(raw.get('round')),
        'spawns': raw.get('spawns') if isinstance(raw.get('spawns'), int) else 0,
        'claims_total': len(claims),
        'claims': count_claims(claims),
        'claim_details': claim_details(claims),
        'bundle': bundle,
        'bundle_exists': bundle_ok,
        'next': raw.get('next'),
        'parse_error': None,
        'excerpt': extract_excerpt(entry),
        'mtime': mtime,
    }


def scan_tasks(project_root: Path, project_id: str, project_resolved: Path,
               docs_ok: bool) -> list:
    """요청 시점 재스캔(캐시 없음) — 링크 아닌 디렉터리 + nofollow state.json만(D9).

    과업당 resolve 0회 — resolve는 프로젝트 루트 1회(요청당 상수)로 제한.
    """
    docs = project_root / DOCS_DIR
    if not dp.is_dir_nofollow(docs):
        return []
    tasks = []
    try:
        with os.scandir(docs) as it:
            entries = [Path(entry.path) for entry in it
                       if not entry.is_symlink()
                       and entry.is_dir(follow_symlinks=False)]
    except OSError:
        return []
    for entry in sorted(entries, key=lambda path: path.name):
        task = load_task(entry, project_root, project_id, project_resolved, docs_ok)
        if task is not None:
            tasks.append(task)
    tasks.sort(key=lambda task: task['task'])
    return tasks


def build_payload(project_root: Path, project_id: str, project_resolved: Path,
                  docs_ok: bool) -> dict:
    """/api/tasks 응답 스키마 — count = tasks 길이(malformed 포함·미보유 폴더 제외)."""
    tasks = scan_tasks(project_root, project_id, project_resolved, docs_ok)
    return {
        'generated_at': utc_now_iso(),
        'project': project_id,
        'count': len(tasks),
        'tasks': tasks,
    }


class DashboardHandler(BaseHTTPRequestHandler):
    """GET 전용 핸들러 — 고정 라우트 + /api/bundle/<folder> 외 전부 404, 비-GET은 405."""

    server_version = SERVER_NAME

    def do_GET(self) -> None:
        if not dp.host_allowed(self.headers.get('Host'),
                               self.server.server_address[1]):
            self.send_not_found()
            return
        route = pure_path(self.path)
        if route in STATIC_ROUTES:
            self.send_static(route)
        elif route == API_ROUTE:
            self.send_api()
        elif route == PROJECTS_API_ROUTE:
            self.send_projects()
        elif route.startswith(BUNDLE_API_PREFIX):
            self.send_bundle(route)
        else:
            self.send_not_found()

    def __getattr__(self, name):
        """미구현 do_* 전부 공통 위임(④리뷰 HIGH) — BaseHTTPRequestHandler 기본은
        501 + stdlib 영어 HTML 에러 페이지라 라우트 계약('그 외 전 메서드 405 ·
        text/plain 고정 단문') 위반이다. OPTIONS·PATCH·TRACE·CONNECT 등 나열
        누락 없이 전 비-GET을 405로 통일한다."""
        if name.startswith('do_'):
            return self.send_405
        raise AttributeError(name)

    def send_static(self, route: str) -> None:
        """고정 매핑 파일 서빙 — 디스크 부재 시 404. 경로 해석 분기는 존재하지 않는다."""
        filename, content_type = STATIC_ROUTES[route]
        target = ASSETS_DIR / filename
        if not target.is_file():
            self.send_not_found()
            return
        try:
            body = target.read_bytes()
        except OSError as exc:
            print(f'정적 자원 판독 실패 {filename}: {exc}', file=sys.stderr)
            self.send_not_found()
            return
        self.send_bytes(200, body, content_type)

    def api_guard(self) -> bool:
        """D8 — /api/* 공통 가드. 위반 시 404 응답 후 False."""
        if not dp.fetch_site_allowed(self.headers.get('Sec-Fetch-Site')):
            self.send_not_found()
            return False
        return True

    def send_projects(self) -> None:
        """/api/projects — 프로젝트 이름만(D2: task_count 미포함)."""
        if not self.api_guard():
            return
        scope = dp.resolve_scope(self.server.root, self.server.workspace)
        projects = [{'id': name} for name in sorted(scope)]
        payload = {
            'generated_at': utc_now_iso(),
            'home': dp.home_project_id(self.server.root),
            'count': len(projects),
            'projects': projects,
        }
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_bytes(200, body, 'application/json; charset=utf-8')

    def send_api(self) -> None:
        # root·workspace는 make_server 이후 서버 인스턴스에 주입된다(main) — 핸들러 클래스
        # 속성으로 두면 기본값('.')이 쓰여 실제 CWD 저장소를 스캔하는 결함이 생긴다
        if not self.api_guard():
            return
        query = self.path.split('?', 1)[1] if '?' in self.path else ''
        try:
            project_id = dp.parse_project_query(query)
        except ValueError:
            self.send_not_found()
            return
        scope = dp.resolve_scope(self.server.root, self.server.workspace)
        if project_id is None:
            project_id = dp.home_project_id(self.server.root)
        project_root = scope.get(project_id)
        if project_root is None:
            self.send_not_found()
            return
        try:
            project_resolved = project_root.resolve()
        except OSError:
            self.send_not_found()
            return
        payload = build_payload(project_root, project_id, project_resolved,
                                dp.docs_prefix_ok(project_root))
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_bytes(200, body, 'application/json; charset=utf-8')


    def send_bundle(self, route: str) -> None:
        """번들 원문 JSON — GET 시점마다 재독(캐시 없음)·nofollow 판독 실패는 404."""
        result = resolve_bundle_route(self.server.root, self.server.workspace,
                                      route[len(BUNDLE_API_PREFIX):])
        if result is None:
            self.send_not_found()
            return
        project_id, bundle_path = result
        markdown = dp.read_text_nofollow(bundle_path)
        if markdown is None:
            print(f'번들 판독 실패 {bundle_path.name}', file=sys.stderr)
            self.send_not_found()
            return
        folder = bundle_path.parent.name
        if project_id is None:
            payload = {'folder': folder, 'markdown': markdown}
        else:
            payload = {'project': project_id, 'folder': folder,
                       'markdown': markdown}
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_bytes(200, body, 'application/json; charset=utf-8')

    def send_not_found(self) -> None:
        self.send_bytes(404, BODY_NOT_FOUND.encode('utf-8'), 'text/plain; charset=utf-8')

    def send_405(self) -> None:
        self.send_bytes(405, BODY_METHOD_NOT_ALLOWED.encode('utf-8'),
                        'text/plain; charset=utf-8')

    def send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def resolve_bundle_route(root: Path, workspace: 'Path | None',
                         rest: str) -> 'tuple[str | None, Path] | None':
    """공유 번들 경로 해석 — DashboardHandler.send_bundle이 위임하는 본체.

    1세그먼트 = 레거시 home(반환 project None — 응답 키 {folder, markdown}
    유지), 2세그먼트 = project/folder(세그먼트별 unquote 1회), 3세그먼트
    이상·빈 세그먼트는 None. 검증 체인은 dashboard_projects 참조(D9·D10·D11).
    """
    if not rest:
        return None
    segments = rest.split('/')
    if len(segments) == 1:
        bundle_path = dp.resolve_task_bundle(root, unquote(segments[0]))
        if bundle_path is None:
            return None
        return None, bundle_path
    if len(segments) != 2 or not segments[0] or not segments[1]:
        return None
    scope = dp.resolve_scope(root, workspace)
    project_id = dp.resolve_project(scope, unquote(segments[0]))
    if project_id is None:
        return None
    bundle_path = dp.resolve_task_bundle(scope[project_id], unquote(segments[1]))
    if bundle_path is None:
        return None
    return project_id, bundle_path


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='r36 과업 상태 HTML 대시보드 (읽기 전용 로컬 서버)')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT,
                        help=f'수신 포트 (기본 {DEFAULT_PORT}, 0 = OS 할당 — READY 라인에서 확인)')
    parser.add_argument('--bind', default=DEFAULT_BIND,
                        help=f'수신 주소 (기본 {DEFAULT_BIND} — 로컬 전용)')
    parser.add_argument('--root', type=Path,
                        default=Path(__file__).resolve().parent.parent,
                        help='state.json 탐색 루트 (기본 = 스크립트 상위 디렉터리)')
    parser.add_argument('--workspace', type=Path, default=None,
                        help='워크스페이스 루트 (지정 시 바로 아래 프로젝트를 함께 '
                             '노출 — 미지정 시 home 단일. 디렉터리가 아니면 exit 2)')
    return parser.parse_args(argv)


def make_server(bind: str, port: int) -> ThreadingHTTPServer:
    """바인드 — 실패는 OSError로 그대로 올린다(main에서 exit 2 사유화)."""
    return ThreadingHTTPServer((bind, port), DashboardHandler)


def install_signal_handlers() -> None:
    """SIGINT/SIGTERM → SystemExit(0) — serve_forever 탈출 후 finally에서 소켓 정리."""

    def terminate(signum, frame):
        raise SystemExit(EXIT_OK)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, terminate)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.bind not in SAFE_BINDS:
        print(f'경고: --bind {args.bind} 은 로컬 전용 기본값({DEFAULT_BIND})이 아니다 — '
              f'외부 노출 시 읽기 전용이라도 상태가 노출된다', file=sys.stderr)
    workspace = None
    if args.workspace is not None:
        if not args.workspace.is_dir():
            print(f'기동 실패: --workspace {args.workspace} 은 디렉터리가 아니다',
                  file=sys.stderr)
            return EXIT_CONFIG_ERROR
        workspace = args.workspace.resolve()
    try:
        server = make_server(args.bind, args.port)
    except OSError as exc:
        print(f'기동 실패: {args.bind}:{args.port} 바인드 오류 — {exc}', file=sys.stderr)
        return EXIT_CONFIG_ERROR
    server.root = args.root.resolve()
    server.workspace = workspace
    bind_host = DEFAULT_BIND if args.bind in ('0.0.0.0', '::') else args.bind
    print(f'READY http://{bind_host}:{server.server_address[1]}', flush=True)
    install_signal_handlers()
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        server.server_close()
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
