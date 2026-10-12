#!/usr/bin/env python3
"""P1 프로젝트 탐색 순수 함수 단위테스트 — U1~U9 (bundle-v3 §P1 RED).

dashboard_projects 모듈을 직접 import해 임시 fixture로 계약을 전수한다.
심볼릭 링크 생성이 OSError로 실패하는 환경(drvfs 등)에서는 링크 의존 케이스를
skip한다 — RED 성립 조건(수집 성공 + 케이스 단위 판정)은 유지된다.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dashboard_projects as dp  # noqa: E402


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def make_project(base: Path, name: str) -> Path:
    """docs/task-id를 갖춘 최소 프로젝트 디렉터리."""
    project = base / name
    (project / 'docs/task-id').mkdir(parents=True, exist_ok=True)
    return project


def try_symlink(target: Path, link: Path) -> bool:
    """심볼릭 링크 생성 — OSError 환경이면 False(스킵 근거). 기존 파일은 치환."""
    try:
        if link.is_symlink() or link.exists():
            link.unlink()
        os.symlink(str(target), str(link))
        return link.is_symlink()
    except OSError:
        return False


class ProjectUnitTests(unittest.TestCase):
    """U1~U9 — 탐색·해석·가드 순수 함수 계약."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        cls.base = base
        cls.ws = base / 'ws'
        cls.ws.mkdir()
        # 유효 프로젝트 2 — 포함 대상
        cls.proj_a = make_project(cls.ws, 'proj-a')
        cls.proj_b = make_project(cls.ws, 'proj-b')
        # 제외 대상 — 숨김·docs 부재·일반 파일
        make_project(cls.ws, '.hidden-proj')
        (cls.ws / 'no-docs').mkdir()
        (cls.ws / 'file.txt').write_text('not a project', encoding='utf-8')
        # home — 워크스페이스 밖
        cls.home = make_project(base, 'home-proj')
        # 링크 fixture — 환경이 링크를 지원하지 않으면 관련 케이스 skip
        cls.outside = base / 'outside'
        write_text(cls.outside / 'secret.md', '외부 비밀')
        write_text(cls.outside / 'external-state.json', '{}')
        write_text(cls.outside / 'external-bundle.md', '# 외부 번들')
        cls.link_ok = True
        cls.link_ok &= try_symlink(cls.proj_a, cls.ws / 'link-proj')
        # docs·task-id가 링크인 프로젝트 — 링크 대상 디렉터리는 미리 만들지 않는다
        (cls.ws / 'docs-link-proj').mkdir()
        cls.link_ok &= try_symlink(cls.proj_a / 'docs',
                                   cls.ws / 'docs-link-proj/docs')
        (cls.ws / 'tid-link-proj/docs').mkdir(parents=True)
        cls.link_ok &= try_symlink(cls.proj_a / 'docs/task-id',
                                   cls.ws / 'tid-link-proj/docs/task-id')
        # resolve_task_bundle 스테이지 fixture — proj-a 안
        tasks = cls.proj_a / 'docs/task-id'
        write_text(tasks / 'ok-task/state.json', '{"task": "ok-task"}')
        write_text(tasks / 'ok-task/bundle.md', '# ok')
        (tasks / 'link-state').mkdir(parents=True)
        cls.link_ok &= try_symlink(cls.outside / 'external-state.json',
                                   tasks / 'link-state/state.json')
        (tasks / 'link-bundle').mkdir(parents=True)
        write_text(tasks / 'link-bundle/state.json', '{"task": "x"}')
        cls.link_ok &= try_symlink(cls.outside / 'external-bundle.md',
                                   tasks / 'link-bundle/bundle.md')
        cls.link_ok &= try_symlink(cls.outside, tasks / 'link-task')
        # U9 — nofollow 읽기 fixture
        write_text(base / 'plain.txt', '본문')

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_u01_discover_projects(self):
        """U1 — 포함·제외·정렬·외부 home·이름 충돌 home 우선·링크 docs 제외."""
        discovered = dp.discover_projects(self.ws)
        names = [name for name, _ in discovered]
        self.assertEqual(names, sorted(names), '이름순 정렬 위반')
        self.assertEqual(names, ['proj-a', 'proj-b'],
                         '숨김·docs 부재·링크·일반 파일 제외 실패')
        paths = dict(discovered)
        self.assertEqual(paths['proj-a'], self.proj_a)
        # 외부 home — 워크스페이스 None이면 home 1개
        scope = dp.resolve_scope(self.home, None)
        self.assertEqual(scope, {dp.home_project_id(self.home): self.home})
        # 이름 충돌 시 home 우선(D4)
        with tempfile.TemporaryDirectory() as raw:
            other_base = Path(raw)
            ws2 = other_base / 'ws'
            ws2.mkdir()
            make_project(ws2, 'dup-proj')
            home2 = make_project(other_base, 'dup-proj')
            dup_scope = dp.resolve_scope(home2, ws2)
            self.assertEqual(dup_scope, {'dup-proj': home2})

    @unittest.skipUnless(os.name == 'posix', '심볼릭 링크 필요')
    def test_u01b_discover_excludes_link_docs(self):
        """U1 보강 — docs·docs/task-id가 링크면 제외(D5)."""
        if not self.link_ok:
            self.skipTest('심볼릭 링크 생성 불가 환경')
        names = [name for name, _ in dp.discover_projects(self.ws)]
        self.assertNotIn('link-proj', names)
        self.assertNotIn('docs-link-proj', names)
        self.assertNotIn('tid-link-proj', names)

    def test_u02_resolve_project(self):
        """U2 — 빈 값·..·슬래시·NUL·숨김·대소문자 변형·링크·workspace None → None."""
        scope = dp.resolve_scope(self.home, self.ws)
        for bad in ('', '..', 'proj-a/b', 'a\0b', '.hidden-proj', 'PROJ-A',
                    'nope', 'no-docs'):
            self.assertIsNone(dp.resolve_project(scope, bad), f'{bad!r} 거부 실패')
        self.assertEqual(dp.resolve_project(scope, 'proj-a'), 'proj-a')
        if self.link_ok:
            self.assertIsNone(dp.resolve_project(scope, 'link-proj'))
        home_scope = dp.resolve_scope(self.home, None)
        self.assertIsNone(dp.resolve_project(home_scope, 'proj-a'),
                          'workspace None인데 워크스페이스 프로젝트가 발견됐다')
        self.assertEqual(dp.resolve_project(home_scope, self.home.name),
                         self.home.name)

    def test_u03_resolve_task_bundle(self):
        """U3 — 단계별 검증 + task·state·bundle 각각 링크 → None."""
        tasks = self.proj_a / 'docs/task-id'
        got = dp.resolve_task_bundle(self.proj_a, 'ok-task')
        self.assertEqual(got, tasks / 'ok-task' / 'bundle.md')
        for bad in ('', 'no-task', '../ok-task', 'ok-task/../..', 'a/b',
                    'ok\\task', 'ok-task\0'):
            self.assertIsNone(dp.resolve_task_bundle(self.proj_a, bad),
                              f'{bad!r} 거부 실패')
        if self.link_ok:
            self.assertIsNone(dp.resolve_task_bundle(self.proj_a, 'link-task'),
                              '과업 디렉터리 링크 미차단')
            self.assertIsNone(dp.resolve_task_bundle(self.proj_a, 'link-state'),
                              'state.json 링크 미차단')
            self.assertIsNone(dp.resolve_task_bundle(self.proj_a, 'link-bundle'),
                              'bundle.md 링크 미차단')

    def test_u04_pick_exact(self):
        """U4 — 대소문자 구분 exact match."""
        self.assertEqual(dp.pick_exact(['alpha'], 'alpha'), 'alpha')
        self.assertIsNone(dp.pick_exact(['alpha'], 'ALPHA'))
        self.assertIsNone(dp.pick_exact(['alpha'], 'alph'))
        self.assertIsNone(dp.pick_exact([], 'alpha'))

    def test_u05_bundle_exists(self):
        """U5 — 표준형 true·프로젝트 내 다른 위치 true·절대경로·..·링크 탈출 false."""
        proj = make_project(self.base, 'oracle-proj')
        write_text(proj / 'docs/task-id/std-task/bundle.md', '# std')
        write_text(proj / 'notes/bundle.md', '# alt')
        resolved = proj.resolve()
        self.assertTrue(dp.bundle_exists('docs/task-id/std-task/bundle.md',
                                         proj, resolved, True))
        self.assertTrue(dp.bundle_exists('notes/bundle.md', proj, resolved, True))
        self.assertFalse(dp.bundle_exists('/etc/hostname', proj, resolved, True))
        self.assertFalse(dp.bundle_exists('../x', proj, resolved, True))
        self.assertFalse(dp.bundle_exists('a/../../x', proj, resolved, True))
        self.assertFalse(dp.bundle_exists('docs/task-id/std-task/missing.md',
                                          proj, resolved, True))
        self.assertFalse(dp.bundle_exists('docs/task-id/std-task/bundle.md',
                                          proj, resolved, False),
                         'docs 접두 불량인데 표준형이 true다')
        self.assertFalse(dp.bundle_exists(None, proj, resolved, True))
        self.assertFalse(dp.bundle_exists('', proj, resolved, True))
        self.assertFalse(dp.bundle_exists('docs/task-id/std-task', proj,
                                          resolved, True),
                         '최종 구성요소가 디렉터리면 false다')
        if self.link_ok:
            self.assertTrue(try_symlink(self.outside, proj / 'linkdir'))
            self.assertFalse(dp.bundle_exists('linkdir/secret.md', proj,
                                              resolved, True),
                             '내부 링크 디렉터리 경유 탈출 미차단')

    def test_u06_host_allowed(self):
        """U6 — Host 허용 목록(소문자 비교, D7)."""
        self.assertTrue(dp.host_allowed('127.0.0.1:5777', 5777))
        self.assertTrue(dp.host_allowed('localhost:5777', 5777))
        self.assertTrue(dp.host_allowed('LOCALHOST:5777', 5777))
        self.assertFalse(dp.host_allowed('evil.example:5777', 5777))
        self.assertFalse(dp.host_allowed('127.0.0.1:9999', 5777), '포트 불일치')
        self.assertFalse(dp.host_allowed(None, 5777), 'Host 누락')
        self.assertFalse(dp.host_allowed('localhost', 5777), '포트 없음')
        self.assertFalse(dp.host_allowed('127.0.0.1', 5777), '포트 없음')

    def test_u07_fetch_site_allowed(self):
        """U7 — Sec-Fetch-Site: 부재 허용, same-origin·none만 허용(D8)."""
        self.assertTrue(dp.fetch_site_allowed(None))
        self.assertTrue(dp.fetch_site_allowed('same-origin'))
        self.assertTrue(dp.fetch_site_allowed('none'))
        self.assertFalse(dp.fetch_site_allowed('same-site'))
        self.assertFalse(dp.fetch_site_allowed('cross-site'))

    def test_u08_parse_project_query(self):
        """U8 — 빈 쿼리 None·project=a 해석·빈 값·키만·중복·필드 11개는 위반."""
        self.assertIsNone(dp.parse_project_query(''))
        self.assertIsNone(dp.parse_project_query('x=1'), 'project 외 키는 무시')
        self.assertEqual(dp.parse_project_query('project=a'), 'a')
        self.assertEqual(dp.parse_project_query('x=1&project=a'), 'a')
        for bad in ('project=', 'project', 'project=a&project=b',
                    'project=&x=1', 'project=a&project='):
            with self.assertRaises(dp.ProjectQueryError, msg=bad):
                dp.parse_project_query(bad)
        eleven = '&'.join(f'f{i}=v' for i in range(11))
        with self.assertRaises(dp.ProjectQueryError):
            dp.parse_project_query(eleven)

    def test_u09_read_text_nofollow(self):
        """U9 — 일반 파일 읽기·부재 None·링크 None."""
        plain = self.base / 'plain.txt'
        self.assertEqual(dp.read_text_nofollow(plain), '본문')
        self.assertIsNone(dp.read_text_nofollow(self.base / 'missing.txt'))
        if self.link_ok:
            link = self.base / 'link.txt'
            self.assertTrue(try_symlink(plain, link))
            self.assertIsNone(dp.read_text_nofollow(link), '링크 읽기 차단 실패')


if __name__ == '__main__':
    unittest.main()
