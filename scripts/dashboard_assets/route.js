'use strict';
// P2a 대시보드 순수 라우트 로직 — DOM·import·전역 참조 0(node --test 대상, D12).
// parseHash: 해시 문자열 → 뷰 라우트(D14) · taskHash: project·folder → 상세 해시
// · createRequestGate: fetch 요청 토큰 게이트(D13 — J5 계약).
// decode는 세그먼트 split 이후 세그먼트별 1회 — 잘못된 % 인코딩은 list로 격리.

const TASK_PREFIX = '#/task/';
const ACTIVITY_HASH = '#/activity';

function listRoute() {
  return { view: 'list', project: null, folder: null };
}

// 상세 해시 = '#/task/<enc(p)>/<enc(f)>' — 1세그먼트는 home 레거시(project null).
// '#/activity'는 예외로 선행 판정하고, 3세그먼트 이상·빈 세그먼트·디코드 실패는
// 전부 list다(D14·J1~J3·J6).
export function parseHash(hash) {
  if (hash === ACTIVITY_HASH) {
    return { view: 'activity', project: null, folder: null };
  }
  if (!hash.startsWith(TASK_PREFIX)) {
    return listRoute();
  }
  const segments = hash.slice(TASK_PREFIX.length).split('/');
  if (segments.length > 2 || segments.some((segment) => segment === '')) {
    return listRoute();
  }
  try {
    if (segments.length === 1) {
      return { view: 'detail', project: null,
        folder: decodeURIComponent(segments[0]) };
    }
    return { view: 'detail', project: decodeURIComponent(segments[0]),
      folder: decodeURIComponent(segments[1]) };
  } catch {
    return listRoute();
  }
}

// 뷰1·뷰2 행 클릭이 쓰는 상세 해시 빌더 — folder의 '/'도 %2F로 인코딩해
// 한 세그먼트를 유지한다(J4). project null은 1세그먼트 레거시(home).
export function taskHash(project, folder) {
  const encodedFolder = encodeURIComponent(String(folder));
  if (project === null || project === undefined) {
    return TASK_PREFIX + encodedFolder;
  }
  return `${TASK_PREFIX}${encodeURIComponent(String(project))}/${encodedFolder}`;
}

// 요청 토큰 게이트(D13·J5) — begin은 새 토큰을 발급하고 이전 토큰을 current에서
// 밀어낸다. end는 pending === seq일 때만 busy를 해제하므로 오래된 응답의 end가
// 새 요청의 busy를 풀 수 없고, isCurrent로 낡은 응답을 폐기한다.
export function createRequestGate() {
  let next = 1;
  let current = 0;
  let pending = 0;
  return {
    begin() {
      current = next;
      pending = next;
      next += 1;
      return current;
    },
    end(seq) {
      if (pending === seq) {
        pending = 0;
      }
    },
    isCurrent(seq) {
      return seq === current;
    },
    isPending() {
      return pending !== 0;
    },
  };
}
