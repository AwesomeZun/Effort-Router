#!/usr/bin/env python3
"""r39 PR-0 — 실 Stagehand 러너의 SDK 표면 계약 단위테스트.

설치된 stagehand 4.1.0 표면을 흉내 낸 가짜 모듈을 sys.modules에 주입해 호출 순서를 검증한다.
- 직접 생성(Stagehand(env=...))은 SDK가 TypeError로 거부하므로 금지 — create() 경로만 쓴다.
- act/observe/extract는 Stagehand 인스턴스 메서드이며 page= 인자로 대상 페이지를 지정한다.
- 토큰 보고는 stagehand.metrics()로 얻는다(P2·P1 원천).
"""
from __future__ import annotations

import asyncio
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stagehand_runner  # noqa: E402


class FakeContext:
    def __init__(self, page):
        self._page = page

    async def active_page(self):
        return self._page


class FakePage:
    def __init__(self, calls):
        self.calls = calls

    async def goto(self, url):
        self.calls.append(('goto', url))


class FakeBrowser:
    def __init__(self, calls):
        self.calls = calls
        self.page = FakePage(calls)
        self.context = FakeContext(self.page)

    async def close(self):
        self.calls.append(('browser.close',))


class FakeStagehand:
    def __init__(self, calls, browser):
        self.calls = calls
        self.browser = browser

    async def act(self, instruction, *, page=None, **_):
        self.calls.append(('act', instruction, page is self.browser.page))
        return {'ok': True}

    async def observe(self, instruction=None, *, page=None, **_):
        self.calls.append(('observe', instruction, page is self.browser.page))
        return [{'selector': 'h1'}, {'selector': 'h2'}]

    async def extract(self, instruction, *, page=None, **_):
        self.calls.append(('extract', instruction, page is self.browser.page))
        return {'extraction': 'Example Domain'}

    async def metrics(self):
        return types.SimpleNamespace(total_prompt_tokens=10.0, total_cached_input_tokens=4.0,
                                     total_completion_tokens=2.0, total_reasoning_tokens=0.0)

    async def close(self):
        self.calls.append(('stagehand.close',))


def install_fake_sdk(calls, create_kwargs):
    """가짜 stagehand 패키지 주입 — launch/create가 호출 기록을 남긴다."""
    module = types.ModuleType('stagehand')
    browser_holder = {}

    class LocalBrowserFactory:
        async def launch(self, **kwargs):
            calls.append(('local_browser.launch', kwargs))
            browser_holder['browser'] = FakeBrowser(calls)
            return browser_holder['browser']

    class Stagehand:
        def __init__(self, *args, **kwargs):
            raise TypeError('Stagehand cannot be constructed directly; use await Stagehand.create()')

        @classmethod
        async def create(cls, **kwargs):
            calls.append(('Stagehand.create', kwargs))
            create_kwargs.update(kwargs)
            return FakeStagehand(calls, kwargs['browser'])

    module.local_browser = LocalBrowserFactory()
    module.Stagehand = Stagehand
    sys.modules['stagehand'] = module


class RunnerSdkContractTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.create_kwargs = {}
        install_fake_sdk(self.calls, self.create_kwargs)
        self.spec = {
            'task_id': 'pr0-contract', 'url': 'https://example.com',
            'steps': [
                {'action': 'act', 'instruction': '접속'},
                {'action': 'observe', 'instruction': '제목 후보'},
                {'action': 'extract', 'instruction': 'h1 텍스트'},
            ],
            'success_criteria': 'h1 표시',
        }

    def tearDown(self):
        sys.modules.pop('stagehand', None)

    def run_runner(self):
        return asyncio.run(stagehand_runner._run_steps(self.spec, 1))

    def test_uses_create_api_not_direct_construction(self):
        result = self.run_runner()
        names = [call[0] for call in self.calls]
        self.assertIn('Stagehand.create', names)
        self.assertEqual(result['status'], 'success')

    def test_create_receives_browser_and_model(self):
        self.run_runner()
        self.assertIn('browser', self.create_kwargs)
        self.assertEqual(self.create_kwargs['model'], 'openai/gpt-4o-mini')

    def test_steps_pass_page_explicitly(self):
        self.run_runner()
        step_calls = [call for call in self.calls if call[0] in ('act', 'observe', 'extract')]
        self.assertEqual([call[0] for call in step_calls], ['act', 'observe', 'extract'])
        self.assertTrue(all(call[2] for call in step_calls), 'page= must be the active page')

    def test_goto_then_close_browser_and_stagehand(self):
        self.run_runner()
        names = [call[0] for call in self.calls]
        self.assertLess(names.index('local_browser.launch'), names.index('Stagehand.create'))
        self.assertIn(('goto', 'https://example.com'), self.calls)
        self.assertEqual(names[-2:], ['stagehand.close', 'browser.close'])

    def test_close_runs_even_when_step_fails(self):
        async def boom(*_a, **_k):
            raise RuntimeError('step failed')

        original = FakeStagehand.act
        FakeStagehand.act = boom
        try:
            with self.assertRaises(RuntimeError):
                self.run_runner()
        finally:
            FakeStagehand.act = original
        names = [call[0] for call in self.calls]
        self.assertIn('stagehand.close', names)
        self.assertIn('browser.close', names)

    def test_metrics_collected_for_token_source(self):
        result = self.run_runner()
        self.assertEqual(result['metrics']['total_prompt_tokens'], 10.0)
        self.assertEqual(result['metrics']['total_cached_input_tokens'], 4.0)


if __name__ == '__main__':
    unittest.main()
