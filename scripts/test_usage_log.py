#!/usr/bin/env python3
"""r39 P1 — usage_log 단위테스트: rollout 추출·숫자 화이트리스트·멱등·원천 부재 exit 2.

픽스처는 합성 JSONL이다(실 대화 원문 미사용). 레코드에 텍스트 필드가 없어야 한다.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import usage_log  # noqa: E402

SYNTHETIC_ROLLOUT = [
    {'timestamp': '2026-10-12T01:00:00.000Z', 'ordinal': 1, 'type': 'turn_context',
     'payload': {'turn_id': 't1', 'model': 'gpt-6-luna', 'cwd': '/secret/path', 'instructions': 'PROMPT TEXT'}},
    {'timestamp': '2026-10-12T01:00:05.000Z', 'ordinal': 2, 'type': 'event_msg',
     'payload': {'type': 'token_count', 'info': {
         'total_token_usage': {'input_tokens': 100, 'cached_input_tokens': 0,
                               'cache_write_input_tokens': 0, 'output_tokens': 5,
                               'reasoning_output_tokens': 0, 'total_tokens': 105},
         'last_token_usage': {'input_tokens': 100, 'cached_input_tokens': 0,
                              'cache_write_input_tokens': 0, 'output_tokens': 5,
                              'reasoning_output_tokens': 0, 'total_tokens': 105}}}},
    {'timestamp': '2026-10-12T01:00:09.000Z', 'ordinal': 3, 'type': 'event_msg',
     'payload': {'type': 'agent_message', 'message': 'RESPONSE TEXT'}},
    {'timestamp': '2026-10-12T01:00:10.000Z', 'ordinal': 4, 'type': 'event_msg',
     'payload': {'type': 'token_count', 'info': {
         'total_token_usage': {'input_tokens': 220, 'cached_input_tokens': 90,
                               'cache_write_input_tokens': 0, 'output_tokens': 12,
                               'reasoning_output_tokens': 3, 'total_tokens': 232},
         'last_token_usage': {'input_tokens': 120, 'cached_input_tokens': 90,
                              'cache_write_input_tokens': 0, 'output_tokens': 7,
                              'reasoning_output_tokens': 3, 'total_tokens': 130}}}},
]

ALLOWED_KEYS = {'ts', 'source', 'role', 'model', 'trace_ref', 'ordinal', 'calls',
                'input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
                'output_tokens', 'reasoning_output_tokens'}


def write_rollout(directory: Path, lines: list[dict]) -> Path:
    path = directory / 'rollout-synthetic.jsonl'
    path.write_text('\n'.join(json.dumps(line) for line in lines) + '\n', encoding='utf-8')
    return path


class ParseRolloutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.rollout = write_rollout(self.dir, SYNTHETIC_ROLLOUT)

    def tearDown(self):
        self.tmp.cleanup()

    def test_one_record_per_token_count_event_using_last_usage(self):
        records = usage_log.parse_rollout(self.rollout, role='coder-medium')
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]['input_tokens'], 100)
        self.assertEqual(records[1]['input_tokens'], 120)
        self.assertEqual(records[1]['cached_input_tokens'], 90)
        self.assertEqual(records[1]['output_tokens'], 7)

    def test_model_taken_from_latest_turn_context(self):
        records = usage_log.parse_rollout(self.rollout, role=None)
        self.assertTrue(all(record['model'] == 'gpt-6-luna' for record in records))

    def test_record_contains_only_whitelisted_keys(self):
        records = usage_log.parse_rollout(self.rollout, role=None)
        for record in records:
            self.assertLessEqual(set(record), ALLOWED_KEYS)

    def test_no_prompt_or_response_text_leaks(self):
        blob = json.dumps(usage_log.parse_rollout(self.rollout, role=None), ensure_ascii=False)
        for forbidden in ('PROMPT TEXT', 'RESPONSE TEXT', '/secret/path'):
            self.assertNotIn(forbidden, blob)

    def test_trace_ref_is_file_name_only(self):
        records = usage_log.parse_rollout(self.rollout, role=None)
        self.assertEqual(records[0]['trace_ref'], 'rollout-synthetic.jsonl')

    def test_no_token_count_raises_source_missing(self):
        path = write_rollout(self.dir, [SYNTHETIC_ROLLOUT[0]])
        with self.assertRaises(usage_log.SourceMissingError):
            usage_log.parse_rollout(path, role=None)

    def test_missing_file_raises_source_missing(self):
        with self.assertRaises(usage_log.SourceMissingError):
            usage_log.parse_rollout(self.dir / 'absent.jsonl', role=None)


class AppendAndSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.rollout = write_rollout(self.dir, SYNTHETIC_ROLLOUT)
        self.store = self.dir / 'usage.jsonl'

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_is_idempotent_on_reextract(self):
        records = usage_log.parse_rollout(self.rollout, role=None)
        first = usage_log.append_records(self.store, records)
        second = usage_log.append_records(self.store, records)
        self.assertEqual((first, second), (2, 0))
        self.assertEqual(len(self.store.read_text(encoding='utf-8').splitlines()), 2)

    def test_summary_totals_and_cache_ratio(self):
        usage_log.append_records(self.store, usage_log.parse_rollout(self.rollout, role=None))
        summary = usage_log.summarize(self.store)
        self.assertEqual(summary['calls'], 2)
        self.assertEqual(summary['input_tokens'], 220)
        self.assertEqual(summary['cached_input_tokens'], 90)
        self.assertAlmostEqual(summary['cache_ratio'], 90 / 220, places=4)

    def test_summary_empty_store_reports_zero(self):
        summary = usage_log.summarize(self.store)
        self.assertEqual(summary['calls'], 0)
        self.assertIsNone(summary['cache_ratio'])

    def test_stagehand_metrics_record_maps_whitelisted_fields(self):
        record = usage_log.stagehand_record(
            {'total_prompt_tokens': 10.0, 'total_cached_input_tokens': 4.0,
             'total_completion_tokens': 2.0, 'total_reasoning_tokens': 0.0},
            task='t', attempt=1, model='openai/gpt-4o-mini')
        self.assertLessEqual(set(record), ALLOWED_KEYS)
        self.assertEqual(record['source'], 'stagehand')
        self.assertEqual(record['input_tokens'], 10)
        self.assertEqual(record['cached_input_tokens'], 4)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPTS / 'usage_log.py'), *args],
                              capture_output=True, text=True, timeout=60)

    def test_extract_then_summary_exit_0(self):
        rollout = write_rollout(self.dir, SYNTHETIC_ROLLOUT)
        task_dir = self.dir / 'task'
        extract = self.run_cli('extract', '--rollout', str(rollout), '--task-dir', str(task_dir),
                               '--role', 'coder-medium')
        self.assertEqual(extract.returncode, 0, extract.stderr)
        summary = self.run_cli('summary', '--task-dir', str(task_dir))
        self.assertEqual(summary.returncode, 0, summary.stderr)
        self.assertEqual(json.loads(summary.stdout)['calls'], 2)

    def test_missing_rollout_exit_2_with_reason(self):
        result = self.run_cli('extract', '--rollout', str(self.dir / 'none.jsonl'),
                              '--task-dir', str(self.dir / 'task'))
        self.assertEqual(result.returncode, 2)
        self.assertIn('reason', json.loads(result.stdout))


if __name__ == '__main__':
    unittest.main()
