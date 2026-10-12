// P2a 순수 라우트 로직 테스트 — J1~J6 (bundle-v3 §P2a RED).
// 실행: node --test scripts/test_dashboard_route.mjs
// route.js는 DOM·import 없는 순수 함수만 export한다(D12).
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { parseHash, taskHash, createRequestGate }
  from './dashboard_assets/route.js';

const LIST = { view: 'list', project: null, folder: null };

test('J1 1세그먼트 상세 해시는 project null(home 레거시)', () => {
  assert.deepEqual(parseHash('#/task/alpha-done'),
    { view: 'detail', project: null, folder: 'alpha-done' });
});

test('J2 2세그먼트는 세그먼트별 decode(공백·한글)', () => {
  const hash = '#/task/' + encodeURIComponent('proj p')
    + '/' + encodeURIComponent('폴더 명');
  assert.deepEqual(parseHash(hash),
    { view: 'detail', project: 'proj p', folder: '폴더 명' });
  assert.deepEqual(parseHash('#/task/a%20b/c%20d'),
    { view: 'detail', project: 'a b', folder: 'c d' });
});

test('J3 3세그먼트·빈 세그먼트·잘못된 % 인코딩은 list', () => {
  for (const hash of ['#/task/a/b/c', '#/task/a/', '#/task//b', '#/task/',
    '#/task/%zz']) {
    assert.deepEqual(parseHash(hash), LIST, hash);
  }
  // %2F는 split 후 정상 디코드(D14) — 폴더값이 되며 목록 미매칭으로 격리된다.
  assert.deepEqual(parseHash('#/task/a/%2F'),
    { view: 'detail', project: 'a', folder: '/' });
});

test('J4 taskHash는 세그먼트별 인코딩 — folder의 / 도 한 세그먼트로 보존', () => {
  assert.equal(taskHash(null, 'a/b'), '#/task/a%2Fb');
  assert.equal(taskHash('proj', 'fold'), '#/task/proj/fold');
  assert.equal(taskHash('p k', 'f/k'), '#/task/p%20k/f%2Fk');
  // 왕복 불변 — project null은 1세그먼트 레거시로
  assert.deepEqual(parseHash(taskHash('proj', 'fold')),
    { view: 'detail', project: 'proj', folder: 'fold' });
  assert.deepEqual(parseHash(taskHash(null, 'fold')),
    { view: 'detail', project: null, folder: 'fold' });
});

test('J5 요청 게이트 — begin 2회면 앞 요청은 current 아님, 오래된 end는 해제 못한다', () => {
  const gate = createRequestGate();
  const first = gate.begin();
  assert.ok(gate.isPending());
  const second = gate.begin();
  assert.ok(!gate.isCurrent(first), '앞 요청이 current로 남았다');
  assert.ok(gate.isCurrent(second));
  gate.end(first); // 오래된 end — 새 요청 busy를 풀지 않는다
  assert.ok(gate.isPending(), '오래된 end가 pending을 해제했다');
  gate.end(second); // 현재 요청 end — 해제
  assert.ok(!gate.isPending());
  assert.ok(gate.isCurrent(second), 'end는 current를 바꾸지 않는다');
});

test('J6 #/와 빈 해시는 list, #/activity는 예외 선행 판정', () => {
  assert.deepEqual(parseHash('#/'), LIST);
  assert.deepEqual(parseHash(''), LIST);
  assert.deepEqual(parseHash('#/other'), LIST);
  assert.deepEqual(parseHash('#/activity'),
    { view: 'activity', project: null, folder: null });
  // 상세 해시와 activity가 섞인 변형은 전부 list
  assert.deepEqual(parseHash('#/activity/extra'), LIST);
});
