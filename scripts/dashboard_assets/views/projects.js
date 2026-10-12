'use strict';
// P2b 뷰 — 사이드바 프로젝트 전환 네비(D1). button + textContent만 주입한다
// (innerHTML·localStorage 금지 — F4). 선택 항목에 aria-current, 과업 수는
// 선택 프로젝트의 state.tasks.length만 표시한다(D2 — 서버는 선택 프로젝트만 스캔).

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) { node.className = className; }
  if (text !== undefined && text !== null) { node.textContent = String(text); }
  return node;
}

// vm: { projects: string[], current: string | null, taskCount: number }
export function renderProjectNav(container, vm, actions) {
  const fragment = document.createDocumentFragment();
  vm.projects.forEach((id) => {
    const button = el('button', 'project-item');
    button.type = 'button';
    button.title = `${id} 과업 보기`;
    button.appendChild(el('span', 'project-name', id));
    if (id === vm.current) {
      button.classList.add('active');
      button.setAttribute('aria-current', 'true');
      button.appendChild(el('span', 'project-count tnum', String(vm.taskCount)));
    }
    button.addEventListener('click', () => actions.selectProject(id));
    fragment.appendChild(button);
  });
  container.replaceChildren(fragment);
  container.hidden = vm.projects.length === 0;
}
