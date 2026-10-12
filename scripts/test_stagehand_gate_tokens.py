#!/usr/bin/env python3
"""r39 P2 — Stagehand 게이트 토큰 상한 단위·CLI 테스트.

- 상한 초과 시 decision=token-cap-exceeded · exit 1 · jev 호출 전 즉시 중단(비용 차단).
- 원천(metrics) 부재 시 상한 미적용 + token_cap=unavailable 로 드러낸다(조용한 무력화 금지).
- 상한 미지정이면 token_cap=off, 기존 판정 경로 무변경.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_jev_judge import MockJevServer
from test_stagehand_gate import GATE, build_gate_env, run_gate, write_task

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import stagehand_gate as gate  # noqa: E402


class EvaluateTokenCapTests(unittest.TestCase):
    def test_off_without_cap(self):
        self.assertEqual(gate.evaluate_token_cap(None, {'input_tokens': 9}, True), 'off')

    def test_unavailable_when_no_metrics_seen(self):
        self.assertEqual(gate.evaluate_token_cap(10, {'input_tokens': 0}, False), 'unavailable')

    def test_exceeded_when_total_over_cap(self):
        self.assertEqual(gate.evaluate_token_cap(10, {'input_tokens': 11}, True), 'exceeded')

    def test_enforced_when_within_cap(self):
        self.assertEqual(gate.evaluate_token_cap(10, {'input_tokens': 10}, True), 'enforced')


class ResolveMaxTokensTests(unittest.TestCase):
    def test_flag_and_env_fallback(self):
        args = gate.parse_args(['--task-file', 'x.json', '--runner', 'mock', '--max-tokens', '50'])
        self.assertEqual(gate.resolve_config(args, {})['max_tokens'], 50)
        args = gate.parse_args(['--task-file', 'x.json', '--runner', 'mock'])
        self.assertEqual(gate.resolve_config(args, {'STAGEHAND_GATE_MAX_TOKENS': '7'})['max_tokens'], 7)
        self.assertIsNone(gate.resolve_config(args, {})['max_tokens'])

    def test_non_positive_rejected(self):
        args = gate.parse_args(['--task-file', 'x.json', '--runner', 'mock', '--max-tokens', '0'])
        with self.assertRaises(gate.GateConfigError):
            gate.resolve_config(args, {})


class LoopTokenCapTests(unittest.TestCase):
    """runner·jev를 주입해 루프의 상한 분기를 결정적으로 검증한다."""

    CONFIG = {'runner': 'mock', 'mock_scenario': 'never-done', 'jev_mode': 'verify-run',
              'jev_cmd': 'unused', 'max_retries': 2, 'save_dir': None, 'timeout': 5.0,
              'max_tokens': None, 'run_id': 'unit'}

    def spec(self):
        return {'task_id': 't', 'url': 'https://e', 'steps': [], 'success_criteria': 's'}

    def outcome_with_metrics(self, prompt):
        return {'status': 'error', 'result': {}, 'error': 'x',
                'metrics': {'total_prompt_tokens': float(prompt),
                            'total_cached_input_tokens': 0.0,
                            'total_completion_tokens': 0.0,
                            'total_reasoning_tokens': 0.0}}

    def run_loop(self, config, outcomes):
        jev_calls = []

        def fake_jev(cfg, statement):
            jev_calls.append(statement)
            return {'unavailable': False, 'consumed': {'confirmed': False, 'noul': 0.1}}

        with mock.patch.object(gate, 'execute_attempt', side_effect=outcomes), \
                mock.patch.object(gate, 'call_jev', side_effect=fake_jev):
            result = gate.run_gate_loop(config, self.spec())
        return result, jev_calls

    def test_exceeded_stops_before_jev_and_next_attempt(self):
        config = dict(self.CONFIG, max_tokens=100)
        result, jev_calls = self.run_loop(config, [self.outcome_with_metrics(150)] * 3)
        self.assertEqual(result['decision'], 'token-cap-exceeded')
        self.assertEqual(result['token_cap'], 'exceeded')
        self.assertEqual(result['attempts'], 1)
        self.assertEqual(jev_calls, [])

    def test_within_cap_follows_normal_retry_path(self):
        config = dict(self.CONFIG, max_tokens=10_000)
        result, _ = self.run_loop(config, [self.outcome_with_metrics(10)] * 3)
        self.assertEqual(result['decision'], 'retry-exhausted')
        self.assertEqual(result['token_cap'], 'enforced')
        self.assertEqual(result['token_usage']['input_tokens'], 30)

    def test_missing_metrics_reports_unavailable_not_silent(self):
        config = dict(self.CONFIG, max_tokens=10)
        no_metrics = {'status': 'error', 'result': {}, 'error': 'x'}
        result, _ = self.run_loop(config, [no_metrics] * 3)
        self.assertEqual(result['decision'], 'retry-exhausted')
        self.assertEqual(result['token_cap'], 'unavailable')

    def test_cap_off_reports_off(self):
        result, _ = self.run_loop(dict(self.CONFIG), [self.outcome_with_metrics(10)] * 3)
        self.assertEqual(result['token_cap'], 'off')


class TokenCapCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_cli_cap_exceeded_exit_1_and_no_jev_call(self):
        with MockJevServer(noul_values={'done': 0.9}) as mock_server:
            result = run_gate('--task-file', write_task(self.directory), '--runner', 'mock',
                              '--mock-scenario', 'retry-then-done', '--max-tokens', '1',
                              mock_server=mock_server)
            requests = len(mock_server.requests)
        self.assertEqual(result.returncode, 1, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output['decision'], 'token-cap-exceeded')
        self.assertEqual(output['token_cap'], 'exceeded')
        self.assertEqual(requests, 0)

    def test_cli_cap_within_pass_with_usage(self):
        with MockJevServer(noul_values={'done': 0.9}) as mock_server:
            result = run_gate('--task-file', write_task(self.directory), '--runner', 'mock',
                              '--mock-scenario', 'done-first', '--max-tokens', '100000',
                              mock_server=mock_server)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output['decision'], 'pass')
        self.assertEqual(output['token_cap'], 'enforced')
        self.assertGreater(output['token_usage']['input_tokens'], 0)

    def test_cli_zero_cap_exit_2(self):
        result = run_gate('--task-file', write_task(self.directory), '--runner', 'mock',
                          '--max-tokens', '0')
        self.assertEqual(result.returncode, 2)

    def test_cli_env_cap_applies(self):
        with MockJevServer(noul_values={'done': 0.9}) as mock_server:
            result = run_gate('--task-file', write_task(self.directory), '--runner', 'mock',
                              '--mock-scenario', 'done-first',
                              env_extra={'STAGEHAND_GATE_MAX_TOKENS': '1'},
                              mock_server=mock_server)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['decision'], 'token-cap-exceeded')


if __name__ == '__main__':
    unittest.main()
