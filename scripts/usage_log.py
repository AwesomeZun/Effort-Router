#!/usr/bin/env python3
"""r39 P1 — 토큰 사용량 수집기. 과업 폴더 usage.jsonl에 숫자 화이트리스트 레코드만 남긴다.

원천 2종:
- Codex rollout JSONL — event_msg/token_count의 last_token_usage(호출 단위), 모델은 직전 turn_context.
- Stagehand — StagehandMetrics 누적값(stagehand_record로 변환해 게이트가 호출).
레코드에는 프롬프트·응답·명령 출력 원문을 넣지 않는다(화이트리스트 키만 허용).

종료코드: 0 성공 / 2 bypass(원천 부재·파손 — reason 필드 동반, jev 폴백 계약 준용).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

USAGE_FILE = 'usage.jsonl'
TOKEN_FIELDS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
                'output_tokens', 'reasoning_output_tokens')


class SourceMissingError(Exception):
    """원천 파일 부재 또는 토큰 이벤트 0건 — exit 2 bypass 근거."""


def _int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _usage_fields(usage: dict[str, Any]) -> dict[str, int]:
    return {name: _int(usage.get(name)) for name in TOKEN_FIELDS}


def parse_rollout(path: Path, role: str | None) -> list[dict[str, Any]]:
    """rollout JSONL에서 token_count 이벤트별 호출 레코드를 뽑는다(텍스트 필드 미포함)."""
    if not path.is_file():
        raise SourceMissingError(f'rollout 파일 없음: {path.name}')
    records: list[dict[str, Any]] = []
    model: str | None = None
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = _parse_line(raw)
        if line is None:
            continue
        payload = line.get('payload') or {}
        if line.get('type') == 'turn_context':
            model = payload.get('model') or model
        if line.get('type') == 'event_msg' and payload.get('type') == 'token_count':
            last = ((payload.get('info') or {}).get('last_token_usage')) or {}
            records.append({'ts': line.get('timestamp'), 'source': 'codex', 'role': role,
                            'model': model, 'trace_ref': path.name,
                            'ordinal': line.get('ordinal'), 'calls': 1,
                            **_usage_fields(last)})
    if not records:
        raise SourceMissingError('token_count 이벤트 0건 — 사용량 원천 부재')
    return records


def _parse_line(raw: str) -> dict[str, Any] | None:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def stagehand_record(metrics: dict[str, float], task: str, attempt: int,
                     model: str) -> dict[str, Any]:
    """Stagehand 누적 메트릭 1시도분 레코드(숫자 필드만)."""
    return {'ts': None, 'source': 'stagehand', 'role': None, 'model': model,
            'trace_ref': task, 'ordinal': attempt, 'calls': 1,
            'input_tokens': _int(metrics.get('total_prompt_tokens')),
            'cached_input_tokens': _int(metrics.get('total_cached_input_tokens')),
            'cache_write_input_tokens': 0,
            'output_tokens': _int(metrics.get('total_completion_tokens')),
            'reasoning_output_tokens': _int(metrics.get('total_reasoning_tokens'))}


def _key(record: dict[str, Any]) -> tuple[Any, Any, Any]:
    return (record.get('source'), record.get('trace_ref'), record.get('ordinal'))


def _existing_keys(store: Path) -> set[tuple[Any, Any, Any]]:
    if not store.is_file():
        return set()
    return {_key(_parse_line(line) or {}) for line in store.read_text(encoding='utf-8').splitlines()}


def append_records(store: Path, records: list[dict[str, Any]]) -> int:
    """중복 키(source·trace_ref·ordinal)는 건너뛰어 재추출을 멱등하게 만든다. 추가 건수 반환."""
    seen = _existing_keys(store)
    fresh = []
    for record in records:
        if _key(record) not in seen:
            seen.add(_key(record))
            fresh.append(record)
    if fresh:
        store.parent.mkdir(parents=True, exist_ok=True)
        with store.open('a', encoding='utf-8') as handle:
            for record in fresh:
                handle.write(json.dumps(record, ensure_ascii=False) + '\n')
    return len(fresh)


def summarize(store: Path) -> dict[str, Any]:
    """usage.jsonl 합계와 캐시 비율(cached/input). 입력 0이면 비율 None."""
    rows = [_parse_line(line) or {} for line in store.read_text(encoding='utf-8').splitlines()] \
        if store.is_file() else []
    totals = {name: sum(_int(row.get(name)) for row in rows) for name in TOKEN_FIELDS}
    calls = sum(_int(row.get('calls')) for row in rows)
    ratio = totals['cached_input_tokens'] / totals['input_tokens'] \
        if totals['input_tokens'] else None
    return {'calls': calls, **totals, 'cache_ratio': ratio}


def _cmd_extract(args: argparse.Namespace) -> int:
    store = Path(args.task_dir) / USAGE_FILE
    try:
        records = parse_rollout(Path(args.rollout), args.role)
    except SourceMissingError as error:
        print(json.dumps({'ok': False, 'reason': str(error)}, ensure_ascii=False))
        return 2
    added = append_records(store, records)
    print(json.dumps({'ok': True, 'added': added, 'store': str(store)}, ensure_ascii=False))
    return 0


def _cmd_summary(args: argparse.Namespace) -> int:
    print(json.dumps(summarize(Path(args.task_dir) / USAGE_FILE), ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='r39 P1 사용량 수집기')
    sub = parser.add_subparsers(dest='command', required=True)
    extract = sub.add_parser('extract', help='rollout에서 호출 단위 레코드 추출')
    extract.add_argument('--rollout', required=True)
    extract.add_argument('--task-dir', required=True)
    extract.add_argument('--role')
    extract.set_defaults(func=_cmd_extract)
    summary = sub.add_parser('summary', help='과업 사용량 합계·캐시 비율')
    summary.add_argument('--task-dir', required=True)
    summary.set_defaults(func=_cmd_summary)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
