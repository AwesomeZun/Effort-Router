'use strict';
// r37 대시보드 앱 코어 — 부트·해시 라우터·상태·fetch·폴링·셸 바인딩.
// 네트워크 호출은 동일 origin fetch('/api/tasks')·fetch('/api/bundle/')·
// fetch('/api/projects') 유일(외부 의존·외부 fetch·클라이언트 저장소 0).
// 뷰 모듈은 순수 렌더 — 상태·이벤트·fetch는 이 파일 단일 소유. 불변 패턴:
// 파생 데이터는 새 배열, 필드 교체만 허용.
//
// 부팅 계약(D16): fetchProjects를 2초 백오프로 3회 시도, 실패하면 폴링 주기의
// 재시도로 편입된다. 프로젝트 결정(상세 해시 → 그 프로젝트, 아니면 home) 뒤
// force fetch가 성공해야 booted=true가 되고 applyRoute로 최초 라우팅 확정 —
// 그 전 hashchange는 무시된다. force fetch 404는 home으로 1회만 전환하고,
// 그 밖의 실패는 다음 폴링에서 재시도한다(급행 무한 재시도 없음).
// 폴링(기본 5초)은 라우트 무관 지속 — 단 상세(뷰3)는 폴링이 문서 DOM을
// 재구성하지 않는다(문서는 진입 시 1회 /api/bundle 페치 — 스크롤·아코디언 보존).

import { renderList } from '/views/list.js';
import { renderActivity } from '/views/activity.js';
import { renderDetail } from '/views/detail.js';
import { renderProjectNav as renderProjectNavView } from '/views/projects.js';
import { parseHash as parseHashRoute, taskHash, createRequestGate }
  from '/route.js';

const DEFAULT_REFRESH_MS = 5000;
const ACTIVE_PHASES = ['implement', 'review', 'verify'];
// 탑바 시계 — 원본 형식(09. 24. 오후 07:14, 초 없음)
const TOPBAR_TIME = new Intl.DateTimeFormat('ko-KR', {
  month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h12',
});
// 통계 행 — 전체 · 진행 중(implement/review/verify) · 판단 대기(plan/adversary/
// blocked) · 완료(done). 나머지 phase는 통계에서 제외(목록에는 표시).
const ACTIVE_SET = new Set(ACTIVE_PHASES);
const DECISION_SET = new Set(['plan', 'adversary', 'blocked']);

// 애플리케이션 상태 — 필드 교체만, 중첩 갱신은 새 객체 대입.
// 프로젝트 확장(D12): projects(이름 목록)·home·project(선택, null = 부팅 전)·
// loading(전환 로딩). reqSeq·pendingSeq는 gate가 소유한다(D13).
const state = {
  tasks: [],
  filters: { phase: '', tier: '', query: '' },
  sort: { key: 'mtime', dir: 'desc' }, // 기본 정렬 = 최근 갱신 최신 상단 (사용자 요구 2026-10-06)
  selected: [],
  expandedDecisions: [],
  docOpen: true,
  intervalMs: DEFAULT_REFRESH_MS,
  timer: null,
  connected: false,
  lastSuccessAt: null,
  route: { view: 'list', project: null, folder: null },
  bundleDoc: null,
  projects: [],
  home: null,
  project: null,
  loading: false,
};

// 요청 토큰 게이트(D13·J5) — force는 항상 발사, 폴링은 in-flight 중 건너뜀,
// 낡은 응답은 isCurrent로 폐기, end는 pending === seq일 때만 해제.
const gate = createRequestGate();

let booted = false;

// --- 순수 함수: 파생 데이터(전부 새 배열) -----------------------------------

function matchesFilters(task, filters) {
  const phaseOk = filters.phase === '' || task.phase === filters.phase;
  const tierOk = filters.tier === '' || task.tier === filters.tier;
  const queryOk = filters.query === ''
    || String(task.task || '').includes(filters.query)
    || String(task.folder || '').includes(filters.query)
    || String(task.excerpt || '').includes(filters.query);
  return phaseOk && tierOk && queryOk;
}

function compareTasks(a, b, key) {
  const va = a[key];
  const vb = b[key];
  if (va === vb) { return 0; }
  if (va === null || va === undefined) { return 1; }
  if (vb === null || vb === undefined) { return -1; }
  if (typeof va === 'number' && typeof vb === 'number') { return va - vb; }
  return String(va).localeCompare(String(vb), 'ko');
}

function visibleTasks() {
  const filtered = state.tasks.filter((task) => matchesFilters(task, state.filters));
  const sign = state.sort.dir === 'desc' ? -1 : 1;
  return filtered.slice().sort((a, b) => sign * compareTasks(a, b, state.sort.key));
}

function computeStats() {
  const total = state.tasks.length;
  let active = 0;
  let decision = 0;
  let done = 0;
  state.tasks.forEach((task) => {
    if (ACTIVE_SET.has(task.phase)) { active += 1; }
    if (DECISION_SET.has(task.phase)) { decision += 1; }
    if (task.phase === 'done') { done += 1; }
  });
  return [
    { label: '전체', value: total },
    { label: '진행 중', value: active },
    { label: '판단 대기', value: decision },
    { label: '완료', value: done },
  ];
}

// --- 해시 라우터 -------------------------------------------------------------

// 순수 해시 해석은 route.js(parseHashRoute) — 여기서는 상태 결합만 한다(D15).
function applyRoute() {
  if (state.loading) { return; } // 로딩 중 폴더 검증 보류(D15)
  const route = parseHashRoute(window.location.hash);
  if (route.view === 'detail') {
    const target = route.project === null ? state.project : route.project;
    if (!state.projects.includes(target)) {
      restoreHash(); // 상세 해시의 project 미발견 — 현재 상태 해시로 복원
      return;
    }
    if (target !== state.project) {
      selectProject(target, true); // 같은 전환 경로를 탄 뒤 재평가(뒤로가기 처리)
      return;
    }
    state.route = route;
    loadBundle(route.folder);
    return; // loadBundle → renderDetailView
  }
  state.route = route;
  renderPoll();
}

// 현재 상태를 해시로 역직렬화 — 해시-상태 불일치 제거용(D15).
function restoreHash() {
  const route = state.route;
  if (route.view === 'detail') {
    window.location.hash = taskHash(
      route.project === null ? state.project : route.project, route.folder);
  } else if (route.view === 'activity') {
    window.location.hash = '#/activity';
  } else {
    window.location.hash = '#/';
  }
}

// 프로젝트 전환(D15) — fromRoute면 해시를 건드리지 않고 전환 완료 후 applyRoute
// 재평가(그 시점 해시가 상세 해시가 아니면 list 렌더로 즉시 종료 = 재귀 종점).
function selectProject(id, fromRoute = false) {
  if (!state.projects.includes(id) || id === state.project) { return; }
  state.project = id;
  state.tasks = [];
  state.selected = [];
  state.expandedDecisions = [];
  state.bundleDoc = null;
  state.loading = true;
  renderLoading(); // D15 — 전환 즉시 명시적 로딩 화면
  if (!fromRoute) {
    state.route = { view: 'list', project: null, folder: null };
    if (window.location.hash !== '#/' && window.location.hash !== '') {
      window.location.hash = '#/'; // hashchange → applyRoute(로딩 중이라 즉시 종료)
    }
  }
  fetchTasks({ force: true }).then((result) => {
    state.loading = false;
    if (result === null) { return; }
    if (fromRoute) {
      applyRoute();
    } else {
      renderPoll();
    }
  });
}

// 뷰1·뷰2 행 클릭 진입 액션 — 뷰3의 유일한 정상 진입 경로(직접 URL 제외).
// '#/task/' 리터럴은 home(레거시 1세그먼트) 형태다(T15·T23 보존 계약).
function navigate(folder) {
  window.location.hash = state.project === null
    ? '#/task/' + encodeURIComponent(folder)
    : taskHash(state.project, folder);
}

// --- 렌더 -------------------------------------------------------------------

function renderShell() {
  document.getElementById('nav-count-list').textContent = String(state.tasks.length);
  const activeCount = state.tasks.filter((task) => ACTIVE_SET.has(task.phase)).length;
  document.getElementById('nav-count-activity').textContent = String(activeCount);

  const route = state.route;
  const navList = document.getElementById('nav-list');
  const navActivity = document.getElementById('nav-activity');
  navList.classList.toggle('active', route.view === 'list');
  navActivity.classList.toggle('active', route.view === 'activity');

  // 프로젝트 네비 + 워크스페이스명(D12·F3)
  renderProjectNavView(document.getElementById('project-nav'), {
    projects: state.projects,
    current: state.project,
    taskCount: state.tasks.length,
  }, actions);
  const workspaceName = document.getElementById('workspace-name');
  if (workspaceName !== null && state.home !== null) {
    workspaceName.textContent = state.home;
  }

  const crumb = document.getElementById('breadcrumb-current');
  if (route.view === 'detail') {
    crumb.textContent = '과업 상세';
  } else if (route.view === 'activity') {
    crumb.textContent = '실행 현황';
  } else {
    crumb.textContent = '과업 목록';
  }

  const conn = document.getElementById('conn-status');
  conn.classList.toggle('bad', !state.connected);
  document.getElementById('conn-text').textContent =
    state.connected ? '연결됨' : '연결 안 됨';
  document.getElementById('last-updated').textContent = state.lastSuccessAt
    ? TOPBAR_TIME.format(state.lastSuccessAt) : '–';
}

function showView(name) {
  ['view-list', 'view-activity', 'view-detail'].forEach((id) => {
    document.getElementById(id).hidden = id !== name;
  });
}

function renderPoll() {
  renderShell();
  if (state.route.view === 'activity') {
    showView('view-activity');
    const container = document.getElementById('view-activity');
    renderActivity(container, {
      active: state.tasks.filter((task) => ACTIVE_SET.has(task.phase)),
      cards: activityCards(),
      expanded: state.expandedDecisions,
    }, actions);
  } else {
    showView('view-list');
    renderList(document.getElementById('view-list'), {
      rows: visibleTasks(),
      stats: computeStats(),
      total: state.tasks.length,
      filters: state.filters,
      sort: state.sort,
      selected: state.selected,
    }, actions);
  }
}

// 전환 로딩 화면(D15) — 빈 상태 렌더 대신 명시적 로딩 문구.
function renderLoading() {
  renderShell();
  showView('view-list');
  const container = document.getElementById('view-list');
  delete container.__listShell; // 골격 캐시 무효화 — renderList가 재구축하게(분리 노드 렌더 방지)
  const note = document.createElement('p');
  note.className = 'empty-state';
  note.textContent = `${state.project} 과업을 불러오는 중…`;
  container.replaceChildren(note);
}

function activityCards() {
  const cards = [];
  const gaps = [];
  state.tasks.forEach((task) => {
    if (task.phase === 'blocked') {
      cards.push({
        key: `blocked:${task.folder}`,
        task,
        reason: task.next === null || task.next === undefined
          ? 'blocked — next 미기재' : `blocked — next: ${task.next}`,
        claims: Array.isArray(task.claim_details) ? task.claim_details : [],
      });
      return;
    }
    const gapClaims = (Array.isArray(task.claim_details) ? task.claim_details : [])
      .filter((claim) => claim.status === 'gap');
    if (gapClaims.length > 0) {
      gaps.push({
        key: `gap:${task.folder}`,
        task,
        reason: `gap claims ${gapClaims.length}건 — 판단 필요`,
        claims: gapClaims,
      });
    }
  });
  return cards.concat(gaps);
}

function renderDetailView() {
  renderShell();
  showView('view-detail');
  const task = state.tasks.find((item) => item.folder === state.route.folder);
  const container = document.getElementById('view-detail');
  renderDetail(container, {
    folder: state.route.folder,
    task,
    bundleDoc: state.bundleDoc,
    docOpen: state.docOpen,
  }, actions);
}

// --- 데이터 ------------------------------------------------------------------

// /api/projects(D6 ①) — 프로젝트 이름 목록 + home. 성공 여부만 반환한다.
async function fetchProjects() {
  try {
    const response = await fetch('/api/projects');
    if (!response.ok) { throw new Error(`api 상태 ${response.status}`); }
    const payload = await response.json();
    state.projects = Array.isArray(payload.projects)
      ? payload.projects.map((item) => item.id) : [];
    state.home = typeof payload.home === 'string' ? payload.home : null;
    return state.home !== null;
  } catch {
    return false;
  }
}

// D13 요청 토큰 — ① 폴링은 in-flight 중 건너뜀 ② force는 항상 발사
// ③ 낡은 토큰의 응답은 폐기 ④ finally에서 pendingSeq === seq일 때만 해제
// ⑤ project가 null(부팅 전)이면 발사 안 함 ⑥ 실패는 문구 표시 후 다음 폴링으로
// 계속. 반환: null = 발사 안 함, 그 외 { ok, status }.
async function fetchTasks({ force = false } = {}) {
  if (state.project === null) { return null; }
  if (!force && gate.isPending()) { return null; }
  const seq = gate.begin();
  try {
    const query = `?project=${encodeURIComponent(state.project)}`;
    const response = await fetch('/api/tasks' + query);
    if (!response.ok) { throw new Error(`api 상태 ${response.status}`); }
    const payload = await response.json();
    if (!gate.isCurrent(seq)) { return { ok: false, status: response.status }; }
    state.tasks = Array.isArray(payload.tasks) ? payload.tasks : [];
    state.connected = true;
    state.lastSuccessAt = new Date();
    // 성공 시점에 오류 문구를 닫는다(④리뷰 LOW) — 뷰3 체류 중에도 잔존하지 않다.
    document.getElementById('boot-error').hidden = true;
    return { ok: true, status: response.status };
  } catch (error) {
    state.connected = false;
    printBootError(`상태 갱신 실패 (${error.message}) — 재시도 중`);
    return { ok: false, status: null };
  } finally {
    gate.end(seq);
  }
}

function printBootError(message) {
  const node = document.getElementById('boot-error');
  node.textContent = message;
  node.hidden = false;
}

async function loadBundle(folder) {
  const project = state.project;
  state.bundleDoc = null;
  renderDetailView();
  try {
    // 1세그먼트 = home 레거시, 2세그먼트 = project/folder(D6 ⑥⑦)
    const suffix = project === null
      ? encodeURIComponent(folder)
      : `${encodeURIComponent(project)}/${encodeURIComponent(folder)}`;
    const response = await fetch('/api/bundle/' + suffix);
    if (!response.ok) { throw new Error(`api 상태 ${response.status}`); }
    const payload = await response.json();
    if (state.route.view !== 'detail' || state.route.folder !== folder
      || state.project !== project) {
      return; // 진입 후 (project, folder) 키 변경 — 폐기(D15)
    }
    state.bundleDoc = { folder, markdown: payload.markdown, ok: true };
  } catch {
    if (state.route.view !== 'detail' || state.route.folder !== folder
      || state.project !== project) {
      return;
    }
    state.bundleDoc = { folder, markdown: null, ok: false };
  }
  renderDetailView();
}

// --- 액션 (뷰 → 앱 코어) ------------------------------------------------------

const actions = {
  navigate,
  selectProject,
  toggleSelect(folder) {
    state.selected = state.selected.includes(folder)
      ? state.selected.filter((name) => name !== folder)
      : state.selected.concat([folder]);
    renderPoll();
  },
  clearSelection() {
    state.selected = [];
    renderPoll();
  },
  copyIds(button) {
    const text = state.selected.join('\n');
    const original = button.textContent;
    const finish = (message) => {
      button.textContent = message;
      setTimeout(() => { button.textContent = original; }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text)
        .then(() => finish('복사됨'))
        .catch(() => finish('복사 실패'));
    } else {
      finish('복사 실패');
    }
  },
  setFilter(patch) {
    state.filters = { ...state.filters, ...patch };
    renderPoll();
  },
  setSort(patch) {
    state.sort = { ...state.sort, ...patch };
    renderPoll();
  },
  toggleSortDir() {
    state.sort = { ...state.sort,
      dir: state.sort.dir === 'asc' ? 'desc' : 'asc' };
    renderPoll();
  },
  toggleDecision(key) {
    state.expandedDecisions = state.expandedDecisions.includes(key)
      ? state.expandedDecisions.filter((name) => name !== key)
      : state.expandedDecisions.concat([key]);
    renderPoll();
  },
  toggleDoc() {
    state.docOpen = !state.docOpen;
    renderDetailView();
  },
};

// --- 폴링·부트 ---------------------------------------------------------------

function stopTimer() {
  if (state.timer !== null) {
    clearInterval(state.timer);
    state.timer = null;
  }
}

function applyInterval(ms) {
  stopTimer();
  state.intervalMs = ms;
  if (ms > 0) {
    state.timer = setInterval(() => {
      if (!booted) {
        boot(); // 부팅 미완료 — 폴링 주기의 재시도로 편입(D16 ①)
        return;
      }
      fetchTasks().then((result) => {
        if (result === null) { return; } // in-flight 게이트 — 건너뜀(D13 ①)
        if (state.route.view !== 'detail') {
          renderPoll(); // 상세 뷰는 문서 DOM 보존 — 셸 카운트만 시간 경과 표시
        } else {
          renderShell();
        }
      });
    }, ms);
  }
}

// D16 부트 — fetchProjects 2초 백오프 3회, 실패 시 폴링 편입. 대상 프로젝트는
// 상세 해시면 그 프로젝트, 아니면 home. force fetch 404는 home으로 1회 전환,
// 그 밖의 실패는 다음 폴링에서 재시도한다.
const BOOT_PROJECT_ATTEMPTS = 3;
const BOOT_BACKOFF_MS = 2000;

function sleep(ms) {
  return new Promise((resolve) => { setTimeout(resolve, ms); });
}

async function boot() {
  if (booted) { return; }
  for (let attempt = 1; attempt <= BOOT_PROJECT_ATTEMPTS; attempt += 1) {
    if (attempt > 1) {
      await sleep(BOOT_BACKOFF_MS);
    }
    if (await fetchProjects()) { break; }
  }
  if (state.home === null) { return; } // 실패 — 다음 폴링 틱이 boot 재호출
  const route = parseHashRoute(window.location.hash);
  let target = state.home;
  if (route.view === 'detail' && route.project !== null) {
    if (state.projects.includes(route.project)) {
      target = route.project;
    } else {
      window.location.hash = '#/'; // 상세 해시 project 미발견 — home 복귀
    }
  }
  state.project = target;
  const first = await fetchTasks({ force: true });
  if (first === null || !first.ok) {
    if (first !== null && first.status === 404 && target !== state.home) {
      state.project = state.home;
      window.location.hash = '#/';
      const second = await fetchTasks({ force: true });
      if (second === null || !second.ok) { return; }
    } else {
      return; // 다음 폴링에서 재시도(급행 무한 재시도 없음)
    }
  }
  booted = true;
  applyRoute();
}

function setupControls() {
  document.getElementById('refresh-now').addEventListener('click', () => {
    // 수동 갱신 = projects + tasks force(D15)
    fetchProjects().then(() => {
      fetchTasks({ force: true }).then((result) => {
        if (result === null) { return; }
        if (state.route.view === 'detail') {
          loadBundle(state.route.folder);
        } else {
          renderPoll();
        }
      });
    });
  });
  const interval = document.getElementById('refresh-interval');
  interval.value = String(DEFAULT_REFRESH_MS);
  interval.addEventListener('change', (event) => {
    applyInterval(Number(event.target.value));
  });
  window.addEventListener('hashchange', () => {
    if (!booted) { return; } // 부팅 확정 시점에 applyRoute가 재평가한다
    applyRoute();
  });
}

function init() {
  setupControls();
  applyInterval(DEFAULT_REFRESH_MS);
  boot();
}

init();
