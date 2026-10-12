#!/usr/bin/env python3
"""대시보드 프로젝트 탐색·경로 해석 순수 함수 — dashboard_server P1 확장.

보안 계약(bundle-v3 §2): 모든 경로 해석은 순수 경로 검사 → 디렉터리 목록
exact match(pick_exact) → nofollow lstat → (번들 API 한정) resolve 포함
관계 순서를 따른다. 절대경로와 `..`는 원천 거부되고, 200 응답에 반사되는
project·folder 값은 목록 매칭에 성공한 실제 이름뿐이다(D11). 읽기 전용 —
어떤 파일도 쓰지 않는다.

성능 계약(§6): 과업당 resolve() 금지(drvfs 항목당 lstat 비용) — resolve는
요청당 상수 횟수(번들 API 2회, 스캔 프로젝트 루트 1회)만, 존재 검사 lstat은
과업당 최대 3회(표준형 bundle 구성요소별 nofollow) 이내로 제한한다.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from urllib.parse import parse_qs, unquote

DOCS_PART = 'docs'
TASK_ID_PART = 'task-id'
ALLOWED_FETCH_SITES = ('same-origin', 'none')


class ProjectQueryError(ValueError):
    """project 쿼리 파라미터 규칙 위반 — 서버는 ValueError로 404 사유화한다."""


def is_dir_nofollow(path: Path) -> bool:
    """링크를 따르지 않는 디렉터리 판정(lstat)."""
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def is_regular_nofollow(path: Path) -> bool:
    """링크를 따르지 않는 일반 파일 판정(lstat)."""
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def read_text_nofollow(path: Path) -> 'str | None':
    """O_RDONLY|O_NOFOLLOW 읽기 — 링크(ELOOP)·부재·디코드 실패는 None(D9)."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    except OSError:
        return None
    try:
        with os.fdopen(fd, 'rb') as handle:
            data = handle.read()
    except OSError:
        return None
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return None


def pick_exact(names, raw: str) -> 'str | None':
    """대소문자 구분 exact match — drvfs 대소문자 반사 차단(D5·D11).

    names는 디렉터리 목록(실제 이름)이므로 매칭 성공값만 200에 반사된다.
    """
    return raw if raw in names else None


def has_unsafe_segment(raw: str) -> bool:
    """경로 세그먼트 순수 검사 — 빈 값·구분자·상위 이동·NUL."""
    return (not raw or '/' in raw or '\\' in raw or '..' in raw or '\0' in raw)


def home_project_id(root: Path) -> str:
    """home 프로젝트 식별자 — 루트 디렉터리 이름(D4)."""
    return root.name


def discover_projects(workspace: Path) -> 'list[tuple[str, Path]]':
    """워크스페이스 바로 아래 프로젝트 목록(이름순, D5).

    포함: 숨김 아님 · 링크 아님 · 디렉터리 · docs·docs/task-id가 링크 아닌
    디렉터리. 그 외 전부 제외 — 스캔 오류는 빈 목록으로 격리한다.
    """
    try:
        with os.scandir(workspace) as it:
            entries = list(it)
    except OSError:
        return []
    found = []
    for entry in sorted(entries, key=lambda e: e.name):
        if entry.name.startswith('.'):
            continue
        if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
            continue
        project = Path(entry.path)
        docs = project / DOCS_PART
        task_id = docs / TASK_ID_PART
        if not is_dir_nofollow(docs) or not is_dir_nofollow(task_id):
            continue
        found.append((entry.name, project))
    return found


def resolve_scope(root: Path, workspace: 'Path | None') -> 'dict[str, Path]':
    """{project_id: project_root} — 워크스페이스 프로젝트 + home(충돌 시 home 우선)."""
    scope = {}
    if workspace is not None:
        for name, project in discover_projects(workspace):
            scope[name] = project
    scope[home_project_id(root)] = root
    return scope


def resolve_project(scope: 'dict[str, Path]', raw: str) -> 'str | None':
    """?project= 해석 — 순수 경로 검사 후 scope exact match. 성공 시 식별자."""
    if has_unsafe_segment(raw) or raw.startswith('.'):
        return None
    return pick_exact(scope, raw)


def docs_prefix_ok(project_root: Path) -> bool:
    """프로젝트 docs/task-id 접두 검사 — 요청당 1회(과업 루프 밖)."""
    docs = project_root / DOCS_PART
    task_id = docs / TASK_ID_PART
    return is_dir_nofollow(docs) and is_dir_nofollow(task_id)


def bundle_exists(bundle, project_root: Path, project_resolved: Path,
                  docs_ok: bool) -> bool:
    """state.json bundle 필드 존재 판정(D10).

    순수 경로 검사 먼저: 비문자열·빈 값·절대경로·`..` → false. 표준형
    docs/task-id/... 은 구성요소별 nofollow lstat(과업당 ≤3회), 그 밖의
    형태만 resolve 후 is_relative_to(project_root)로 프로젝트 루트 탈출을
    차단한다(내부 링크 디렉터리 경유 탈출 포함).
    """
    if not isinstance(bundle, str) or not bundle:
        return False
    parts = bundle.split('/')
    if Path(bundle).is_absolute() or '..' in parts:
        return False
    if len(parts) >= 3 and parts[0] == DOCS_PART and parts[1] == TASK_ID_PART:
        if not docs_ok:
            return False
        current = project_root / DOCS_PART / TASK_ID_PART
        last_index = len(parts) - 1
        for index, part in enumerate(parts[2:], start=2):
            current = current / part
            if index == last_index:
                if not is_regular_nofollow(current):
                    return False
            elif not is_dir_nofollow(current):
                return False
        return True
    try:
        target = (project_root / bundle).resolve()
    except OSError:
        return False
    return target.is_relative_to(project_resolved) and target.is_file()


def resolve_task_bundle(project_root: Path, folder_raw: str) -> 'Path | None':
    """번들 API 과업 해석(D9) — 단계별 검증, 링크는 각 단계에서 거부.

    순수 검사 → docs/task-id nofollow → 목록 exact match → state.json·
    bundle.md nofollow lstat → resolve 포함 관계(요청당 resolve 2회).
    """
    if has_unsafe_segment(folder_raw):
        return None
    docs = project_root / DOCS_PART / TASK_ID_PART
    if not is_dir_nofollow(docs):
        return None
    try:
        with os.scandir(docs) as it:
            names = [entry.name for entry in it
                     if not entry.is_symlink()
                     and entry.is_dir(follow_symlinks=False)]
    except OSError:
        return None
    folder = pick_exact(names, folder_raw)
    if folder is None:
        return None
    task_dir = docs / folder
    if not is_regular_nofollow(task_dir / 'state.json'):
        return None
    bundle_path = task_dir / 'bundle.md'
    if not is_regular_nofollow(bundle_path):
        return None
    try:
        if not task_dir.resolve().is_relative_to(docs.resolve()):
            return None
    except OSError:
        return None
    return bundle_path


def host_allowed(host: 'str | None', port: int) -> bool:
    """D7 — Host 허용 목록(소문자 비교): 127.0.0.1:<포트>·localhost:<포트>만.

    누락·포트 없음·다른 포트·타 호스트는 전부 거부한다(DNS 리바인딩 방어).
    """
    if host is None:
        return False
    return host.lower() in (f'127.0.0.1:{port}', f'localhost:{port}')


def fetch_site_allowed(value: 'str | None') -> bool:
    """D8 — Sec-Fetch-Site: 부재 허용, same-origin·none만 허용."""
    return value is None or value in ALLOWED_FETCH_SITES


def parse_project_query(query: str) -> 'str | None':
    """project 쿼리 파라미터 해석(D6 ④⑤).

    빈 쿼리·project 외 키만 있으면 None(home). project= 해석 성공 시 값.
    빈 값·키만·중복·필드 11개 이상(max_num_fields 위반 ValueError)은
    ProjectQueryError(ValueError) — 서버가 404 사유화한다.
    """
    if not query:
        return None
    try:
        fields = parse_qs(query, keep_blank_values=True, max_num_fields=10)
    except ValueError as exc:
        raise ProjectQueryError('too many query fields') from exc
    values = fields.get('project')
    if values is None:
        for field in query.split('&'):
            if field.split('=', 1)[0] == 'project':
                raise ProjectQueryError('project field without value')
        return None
    if len(values) != 1:
        raise ProjectQueryError('duplicate project field')
    if not values[0]:
        raise ProjectQueryError('blank project value')
    return unquote(values[0])
