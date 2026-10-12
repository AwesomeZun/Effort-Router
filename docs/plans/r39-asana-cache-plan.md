# r39 계획서 — Asana 브라우저 에이전트 보고서의 Effort-Router 적용

- task-id: `r39-asana-cache-plan` · 티어: L(§1 산출 문서 고정 승격) · 작성: 2026-10-12
- 상태: ②적대 검토 1라운드 반영본(rev3 — HIGH rev2 + MEDIUM·LOW rev3) — 병합 결과는 §11

## 0. 원 요구 원문

> 보고서를 읽고 https://openai.com/ko-KR/index/asana-browser-agent/ 적용방법을 연구하라
> 계획서 저장 하고 문서 적대 검토해줘

## 1. 결론 (BLUF)

1. **측정 계층(P1)이 최우선이다.** Asana 절감의 원천은 모델 교체가 아니라 프롬프트 캐시 보존이다. 이 저장소에 **캐시 토큰 기록은 없다** — jev_judge는 in/out 토큰만 감사 기록하고(`scripts/jev_judge.py:289`), Stagehand 게이트는 jev usage를 받고도 버린다(`stagehand_gate.py` usage 0건). 측정 없는 캐시 최적화는 NeverStuck C1("Derived, not fitted") 위반 경로다.
2. **원천 카운터는 이미 존재한다** — Codex rollout 로그(`~/.codex/sessions/**/rollout-*.jsonl`)에 `cached_input_tokens` 실측값, Stagehand SDK에 `metrics()`·`total_cached_input_tokens`. 새로 계측할 것이 아니라 **수집·보관**하면 된다.
3. **선행 결함 발견**: Stagehand 실 러너는 핀 SDK 4.1.0에서 생성 단계부터 실패한다(PR-0). P2는 이 수리 없이 실경로 검증 불가.

## 2. 원천 요약

| 출처 | 성격 | 신뢰도 |
|---|---|---|
| openai.com/ko-KR/index/asana-browser-agent | OpenAI 고객 사례(홍보성) | 중 — 수치 원천은 Asana |
| asana.com/inside-asana/cut-browsers-agent-cost | Asana 1차 보고(자기 보고, 독립 재현 없음) | 중상 |
| Asana 전문 PDF | 방법론·한계 | **미열람**(§8 미확정) |

핵심 발견:

1. 도구·시스템 프롬프트만 캐시, 증가하는 이력(페이지 텍스트·스크린샷)은 미캐시 → 매 호출 정가.
2. 매 스텝 스크린샷 삭제·텍스트 트리밍이 앞부분을 바꿔 재사용 붕괴 → **일괄 프루닝 20:1** — 약 19회 연속 재사용. Sol 최적 조건에서 **입력의 89%를 캐시에서 읽음**(원문 "reading 89% of its input from cache").
3. **캐시 단독은 역효과** — 일괄 프루닝 없이 큰 예산에서 이력 캐시 시 4모델 중 3모델 비용 증가.
4. **예산은 모델에 맞춘다** — 12만 자: 모델 C 0/18, Sol 3/18 응답 / 48만 자: 전 실행 응답·정확.
5. 모든 세션 요청·추적·결과를 기록 시스템(Command, MCP 접근)에 보관 → 타 에이전트가 사후 전체 추적 검토 → 티켓 → PR.
6. 사람은 `/goal`·기준·결론 검토, 에이전트가 감사·계측·변수 선별·병렬 실험형 리팩터링·실행·분석.
7. 가드레일 — 실행당 스텝·토큰·비용 상한. 컴팩션은 범위 밖.

수치 해석 주의:

- "76배" = 모델 B 원 설정 → Sol 최적. 동일 모델 B 최적화 효과는 **29배**. 원 설정 일부가 스텝 한도 도달 → 배율은 하한.
- 조건당 3회, 과업 1종(OpenAI 기사: 공개 데모 도서 32권 × 6항목, Asana: 192 사실). 원문 자인: 수 % 차이 구분 불가.
- 후속 12회: 스크린샷 **전량 보존**이 최적 조건보다 호출당 1.2배 저렴(B·Sol), C 약 5%. 프루닝은 긴 작업·작은 창·비싼 캐시 읽기에서만 의미.
- 캐시 읽기 단가: 원문 "표준 입력가 0.05~0.1배"(시험 모델). 타 2차 출처(개인 블로그, 2026-07)는 OpenAI 할인 50% — 불일치, 캐시 **쓰기** 할증 유무도 하니스별 상이 → **결론 #3의 전이 여부는 단가 구조 실측 후 판단**.

### 2.1 교훈 → 적용 대응표

| Asana 교훈("What we took away") | 적용 | 비고 |
|---|---|---|
| 이력까지 캐시 | P3(간접) | 캐시 마커는 하니스 관리 — 직접 제어 불가 |
| append-only·일괄 프루닝 | P3(간접)·P7 | 컴팩션 정책이 유일 레버 |
| 예산을 모델에 맞춤 | **P7** | 모델별 컴팩션·컨텍스트 임계 |
| 공급자 카운터로 호출당 측정 | **P1** | rollout·metrics() 원천 |
| 전 세션 기록 보관 | **P6** | rollout 경로 참조 보관 |
| 스텝·토큰·비용 상한 | **P2** | 비용($)은 단가표 부재로 후속 |

## 3. 수정 대상 (우선순위: 확인된 결함 → 미충족 요구 → 확장)

### PR-0. Stagehand 러너 SDK 표면 수리 — 확인된 결함

- 증거: `.venv-stagehand/.../stagehand/stagehand.py:103-106` 직접 생성 시 `TypeError("Stagehand cannot be constructed directly; use await Stagehand.create()")` ↔ `scripts/stagehand_runner.py:115` `Stagehand(env='LOCAL', model_name=...)`. act/observe/extract는 `page`가 아니라 `Stagehand` 인스턴스 메서드(`stagehand.py:288-403`). 러너의 SDK 호출 표면 전체 — 생성·`init`·`goto`·`act/observe/extract`·`close`(runner:113-128) — 를 4.1.0 공식 표면과 대조해 일괄 수리한다.
- 대상 파일: `scripts/stagehand_runner.py`, `scripts/test_stagehand_gate.py`.
- r21 캐베("실경로 미검증")를 "실경로 결함 확인·수리"로 갱신 → SKILL.md 해당 단락 1곳.

### P1. 사용량 수집 계층

- **기록 위치: 과업 폴더 `docs/task-id/<task-id>/usage.jsonl`** — state.json 신규 필드 0(SKILL.md:428 '`spawns` 유일 예외'·r29 선례 준수).
- **writer**: 메인 세션(스폰 단위 — 완료 후 rollout에서 추출) / 게이트 스크립트(자기 실행분 — jev `--save` 감사 계약 평행, 게이트 고유 파일). state.json writer 원칙 무영향.
- **레코드 스키마(숫자 화이트리스트)**: `{ts, source: codex|stagehand|jev, role?, model, input_tokens, cached_input_tokens, cache_write_input_tokens?, output_tokens, reasoning_output_tokens?, wall_ms, calls}` — **프롬프트·응답·명령 출력 원문 금지**.
- **단위**: 원천이 호출 단위를 주면 호출 단위(Codex rollout은 턴별) — 원문 교훈 "per call"; 아니면 스폰 합계 + 사유 표기.
- **원천**: Codex = rollout JSONL의 token_count 이벤트 / Stagehand = `metrics()` / jev = 기존 응답 usage(게이트가 버리던 값 보존).
- **Claude Code Agent 스폰**: 메인이 받는 결과는 텍스트 + 요약 usage 뿐, cached 분리는 미확인·Z.ai 프록시 경유 → **가정(C1')**. 미확인 시 해당 하니스는 `cached_input_tokens: null`.
- **대시보드**: 과업 상세 뷰에 합계·캐시 비율 표시 — 숫자 키 화이트리스트 유지(`dashboard_server.py:105` 방식).
- **인터페이스**: `python3 <skill-dir>/scripts/usage_log.py extract --rollout <path> --task <task-id> [--role <name>]`(rollout → 레코드 append) · `usage_log.py summary --task <task-id>`(합계·캐시 비율 JSON). 종료코드: 0 성공 / 2 bypass(원천 부재·파손 — jev 폴백 계약 준용). stdlib만 사용.
- **미러**: SKILL.md 신규 절이 `<skill-dir>/scripts/usage_log.py`를 참조하므로 **deploy_global 매니페스트 동봉 대상**(verify_pin·tree_gate와 같은 무의존 스크립트). 게이트 측 기록(stagehand_gate)은 미동봉 유지.
- 대상 파일: `scripts/usage_log.py`(신규), `scripts/stagehand_gate.py`, `scripts/dashboard_server.py`, `scripts/test_usage_log.py`(신규), `scripts/deploy_global.py`(매니페스트), SKILL.md 신규 절.

### P2. Stagehand 게이트 토큰 상한 (PR-0 선행)

- 현행 상한은 호출 수뿐(최악 300회, SKILL.md:135) · 스텝 상한은 기존(`stagehand_gate_policy.py:16` MAX_STEPS 50).
- 추가 인터페이스: `--max-tokens N`(env `STAGEHAND_GATE_MAX_TOKENS`) — 시도 후 `metrics()` 누적 비교, 초과 시 exit 1 + JSON `decision: "token-cap-exceeded"`(retry-exhausted와 구분 — 오진 방지). metrics 획득 불가 시 상한 미적용 + `token_cap: "unavailable"` 경고 필드(조용한 무력화 노출) — 종료코드는 바꾸지 않는다. exit 2 bypass 안(위험 렌즈 #7)은 기각: 브라우저 시도가 이미 과금된 뒤라 bypass 의미(“이 계층 없이 진행”)와 맞지 않고, 호출 수 상한(기존)이 여전히 작동한다.
- **`--max-cost-usd`는 제외** — 저장소에 단가표 원천 0건, 모델 env 가변. §9 후속.
- 원천은 `metrics()`이며 P1 state에 의존하지 않음 → **P1과 병렬 가능**.
- 대상 파일: `scripts/stagehand_gate.py`, `scripts/stagehand_gate_policy.py`, `scripts/stagehand_runner.py`, `scripts/test_stagehand_gate.py`, GATES.md.

### P3. 프롬프트 프리픽스 감사

- 확인: `platforms/codex-agents/*.toml` 10종 동적 값 0(정적 — 양호).
- 점검 대상 훅(`.claude/settings.json` 등록분): `tree_claim_hook.py`(SessionStart·prebash additionalContext), jev 훅 prompt/logbash/guard — 주입 위치가 시스템 프리픽스 앞인지 대화 말미인지.
- 규칙 후보: 정적(SKILL·역할 지침) 앞, 가변(state·과업·주입 데이터) 뒤.
- **효과 근거 없음 주의** — 캐시 마커·프리픽스는 하니스가 관리. 규칙 명문화는 **P1로 전후 캐시 비율 비교가 가능해진 뒤**에만 채택.
- 대상 파일: SKILL.md(채택 시), 보고서 `docs/task-id/r39-asana-cache-plan/p3-audit.md`.

### P4. 티어 라우팅 통제 실험 하네스

- Asana 방법론 이식: 픽스처 × 티어/모델/에포트 × 3회 → 비용·시간·인수 기준 통과율.
- **착수 게이트(전부 충족 전 PR-4 금지)**: (a) 사용자 예산 상한 확정 (b) 실험 실행 경로(`codex exec`·Agent 스폰) 누적 토큰 상한 장치 (c) 픽스처 최소 티어별 2종 이상·대표성 근거 (d) 효과 크기 임계 사전 선언(조건당 3회로 수 % 차이 판정 금지 — Asana 한계 승계).
- **정책 반영 게이트**: 결과로 SKILL.md 매핑 변경 시 사용자 승인 필수 — SKILL.md는 미러 배포 대상(`deploy_global.py:28`)이라 전 환경 전파.
- 대상 파일: `scripts/route_experiment.py`(신규), `scripts/fixtures/route_experiment/*`(신규), 테스트 1종.

### P6. 세션 추적 보관 참조

- Asana Command 대응물 중 state.json·번들은 phase·claims만 보관, 요청·추적 원문 없음.
- 원문 보관은 이미 Codex rollout이 수행 → **복제하지 않고 rollout 파일 경로를 `usage.jsonl` 레코드에 참조로 기록**(`trace_ref`). 사후 다중 에이전트 추적 검토는 이 참조로 가능.
- 보관 기간·rollout 회전 정책은 미확인(§8).

### P7. 모델별 예산(컴팩션·컨텍스트 임계) 조사

- r30에서 모델 전환(Sol 6.1) 시 컴팩션·컨텍스트 임계가 모델에 맞게 재유도됐는지 조사 — Asana #4 "budget has to fit the model".
- 산출: 조사 보고서만(설정 변경은 P1 실측 후 별도 과업).

## 4. 비교표

| 항목 | 비용 대비 효과 | 도입 리스크 | 확장성 | 예상 티어 |
|---|---|---|---|---|
| **PR-0 러너 수리** | 높음(P2 전제·기존 결함) | 낮음 | — | M |
| **P1 사용량 수집** | **높음** | 낮음(과업 폴더 파일) | 높음 | M |
| P2 토큰 상한 | 중 | 낮음 | 중 | M |
| P3 프리픽스 감사 | 미상(효과 근거 없음) | 매우 낮음 | 낮음 | S |
| **P4 통제 실험** | **매우 높음** | 중~높음(비용·과적합) | 높음 | L |
| P6 추적 참조 | 중 | 낮음 | 중 | S(P1 흡수) |
| P7 예산 조사 | 중 | 매우 낮음 | 중 | S |

## 5. PR 분할

| PR | 내용 | 의존 | 미러 영향 |
|---|---|---|---|
| PR-0 | 러너 SDK 수리 | 없음 | stagehand_*는 미동봉(deploy_global.py:39-48) — SKILL.md 캐베 문구만 미러 |
| PR-1 | P1 + P6 | 없음 | SKILL.md 신규 절 → deploy_global 실행 |
| PR-2 | P2 | PR-0 | 미동봉 스크립트 + GATES.md(구현 미배포 사실 문구 명시) |
| PR-3 | P3 감사 · P7 조사 | PR-1(채택 판단에 실측 필요) | 채택 시 SKILL.md·TOML → AGENTS.md 접두사 중복 점검 규칙 적용 |
| PR-4 | P4 | PR-1 + 착수 게이트 | 정책 반영은 별도 사용자 승인 |

병렬 트랙: {PR-0 → PR-2}, {PR-1 → PR-3, PR-4}. 직렬 체인 최장 2 — 상한 5 이내.

## 6. 보존 제약

- `state.json` 스키마 8키 무변경·**신규 필드 0**·writer = 메인 단일.
- Stagehand 게이트 기존 종료코드·decision 값·플래그 기본값 무변경(신규 decision 값 추가만).
- 대시보드 3뷰 라우팅·포트 5777·127.0.0.1 바인드 무변경.
- 외부 전송 신규 경로 0, 사용량 로그에 원문 텍스트 0.
- 문서: README는 요약만, 상세는 CHANGELOG·GATES.md(기존 분리 원칙). 각 PR은 CHANGELOG 1항 추가 의무, SKILL.md 변경 PR은 머지 후 `deploy_global.py --dry-run` → 실배포(미러 드리프트 0 확인).
- 파일 크기: 변경·신규 파일 모두 650줄 경고선·800줄 상한 준수 — PR 완료 보고에 `wc -l` 수치 기재.

## 7. 검증 요구 (claims)

- C1: Codex 캐시 토큰 원천 실측 — rollout JSONL에 `cached_input_tokens` 수치 존재(**증거는 필드명·수치만, 원문 발췌 금지**). `--json` 이벤트 스트림 노출은 별도 확인.
- C1': Claude Code Agent 스폰 결과에서 cached 분리 수치 획득 가능 여부 실측(가능/불가 어느 쪽이든 증거 1건).
- C2: PR-0 후 `.venv-stagehand/bin/python` 실 러너가 생성 단계 TypeError 없이 example 태스크 1회 실행(exit 0 또는 원인 명시된 비생성 실패).
- C3: P1 — mock 실행 1회 후 `usage.jsonl`에 화이트리스트 키만 존재(스키마 테스트 exit 0), 텍스트 필드 0.
- C4: P2 — mock 러너로 토큰 상한 초과 시 exit 1 ∧ `decision == "token-cap-exceeded"`, metrics 부재 시 `token_cap: "unavailable"`.
- C5: 기존 회귀 `for t in scripts/test_*.py` FAIL 0건(각 PR).
- C6: P3 — 등록 훅 전수의 주입 위치 표(훅명·이벤트·위치) 산출.
- C7: P4 — 착수 게이트 4항 충족 기록이 PR-4 착수 전 번들에 존재.
- C8: 본 문서 §2 수치가 원문 2건과 대조 일치 ∧ §2.1 교훈 6항 전부가 P항목 또는 제외 사유에 대응.

## 8. 근거 수준 분리

| 항목 | 수준 | 근거·확인 수단 |
|---|---|---|
| 캐시 토큰 기록 부재(jev in/out만) | 확정 | `jev_judge.py:289`, stagehand_gate usage 0 |
| 역할 TOML 정적 프리픽스 | 확정 | ls 10종·패턴 grep 0 |
| Stagehand 상한 = 호출 수, MAX_STEPS 50 | 확정 | SKILL.md:135, policy:16 |
| 실 러너 생성 실패 | 확정(정적) | SDK stagehand.py:103-106 ↔ runner:115 |
| Stagehand 토큰·캐시 보고 API | 확정(정적) | `_generated/models.py:2178`, `metrics()` |
| Codex rollout 캐시 토큰 | 확정 | `~/.codex/sessions` 실측 `cached_input_tokens` |
| `codex exec --json` 스트림 캐시 토큰 | 가정 | 바이너리 필드명 존재 — C1 실측 |
| Claude Code Agent 캐시 토큰 노출 | 가정 | C1' |
| 하니스별 캐시 할인·쓰기 할증·TTL | 가정 | P1 실측 |
| rollout 보관 기간·회전 | 가정 | Codex 설정·문서 |
| C2 실실행용 LLM 키(OPENAI/ANTHROPIC) 확보 | 가정 | 환경변수 존재 확인 — 부재 시 C2는 생성 단계 통과까지만 판정 |
| SKILL.md:134 "Stagehand는 토큰 수를 보고" 서술 | 확정 | SDK `metrics()` 정적 확인 — 문구 유지 |
| P4 예산 상한 | 미확정 | 사용자만 답함 |
| "보고서" 범위에 Asana PDF 포함 여부 | 미확정 | 사용자만 답함 |

## 9. 후속 개선

- `--max-cost-usd` — 단가표 원천·갱신 주기 정의 후.
- 야간 `/goal` 무인 실행(구 P5) — 외부 쓰기 금지 목록(push·PR·배포·salvage) + 영수 기록 + P4 상한 장치 선행. 본 계획 범위 밖.
- Asana PDF 열람 후 방법론 반영.
- 컴팩션 정책과 캐시 보존의 상호작용(P7 결과 후).

## 10. 파일 소유권

PR별 대상 파일은 §3 각 항목의 '대상 파일'이 진실 원천. 서브에이전트는 자기 PR 파일만 수정, state.json·usage.jsonl 병합은 메인.

## 11. ② 적대 검토 병합 (round.adversary 0)

| 렌즈 | CRITICAL | HIGH | MEDIUM | LOW |
|---|---|---|---|---|
| 완전성 | 0 | 5 | 5 | 2 |
| 기술적 오류 | 0 | 4 | 5 | 4 |
| 위험 | 0 | 4 | 4 | 1 |

메인 교차 확인·처분:

- usage를 state.json에 두는 결함 — 3렌즈 중 3개 지적(완전성 #2·기술 H2·위험 #1) → 확정, P1 위치 이전.
- 토큰 기록 0건 서술 오류(기술 H1) — grep 확인 → 확정, §1·§8 정정.
- 실 러너 SDK 불일치(기술 H4) — SDK 원문 확인 → 확정, PR-0 신설.
- Claude Code 경로 미정(완전성 #8·기술 H3·위험 #6) → C1' 신설.
- C1 원문 유출(위험 #4) → 필드명·수치만으로 제한.
- P4 예산·과적합·정책 전파(위험 #2·#3) → 착수·반영 게이트.
- 추적 보관·예산-모델 누락(완전성 #1·#6) → P6·P7.
- claims 약화·P별 기준 부재(완전성 #4·#5) → C2~C8 재작성.
- **기각**: 기술 M5("도서 32권 × 6필드 원문에 없음") — OpenAI 한국어 기사 원문에 "책 32권에 대해 각각 6개 항목" 명시. 렌즈가 해당 기사 403으로 미대조.

rev3 — MEDIUM·LOW 처분:

| 발견 | 처분 |
|---|---|
| 완전성 #6 예산-모델 / #7 호출 단위 / #9 PDF / #10 훅 열거 / #12 P5 범위 | rev2 반영(P7·P1 단위·§8 미확정·P3 훅 목록·§9 이동) |
| 완전성 #3 인터페이스 | P1 `usage_log.py` CLI·종료코드 정의 |
| 완전성 #11 문서·매니페스트 | §6 CHANGELOG·deploy_global 의무, P1 매니페스트 동봉 |
| 기술 M1·M2·M3·M4 | rev2 반영(§8 확정 전환·`--max-cost-usd` 제외·PR-2 의존 정정) |
| 기술 L1·L3 | rev2 반영(89% 표현·병렬 트랙 표기) |
| 기술 L2 종료코드 | 신규 decision 값으로 구분 — §6 문구와 정합 |
| 기술 L4 호출 범위 | PR-0에 init·goto·close 포함 |
| 위험 #5 P5 / #6 외삽 / #8 미러 / #9 대시보드 | rev2 반영(§9·§2 단가 주의·P3 효과 주의·PR 표 미러 열·화이트리스트) |
| 위험 #7 상한 무력화 | `unavailable` 경고 채택, exit 2 bypass 안 **기각**(근거 P2) |
| 실행 가정 | C2 LLM 키 가정 §8 등록 |

## 12. 구현 상태 (2026-10-12, 미커밋)

| 항목 | 상태 | 증거 |
|---|---|---|
| PR-0 러너 SDK 표면 수리 | 완료 | `scripts/test_stagehand_runner.py` 6/6 (RED→GREEN, 설치 SDK 표면 대조) |
| P1 usage 수집 | 완료 | `scripts/test_usage_log.py` 13/13 · 실 rollout 스모크 45회·캐시 비율 96.6%·텍스트 누출 0 |
| P2 토큰 상한 | 완료 | `scripts/test_stagehand_gate_tokens.py` 14/14 · `--max-cost-usd` 제외(§9) |
| P3 프리픽스 감사 | 완료(보고만) | `docs/task-id/r39-asana-cache-plan/p3-audit.md` — 채택 보류(P1 실측 후) |
| P7 모델 예산 조사 | 완료(보고만) | `p7-budget-survey.md` — 가설 H-P7: 임계 700k > 보고 창 258k |
| P6 추적 참조 | P1에 흡수 | `usage.jsonl` trace_ref |
| P4 통제 실험 | **보류** | 착수 게이트 4항 중 (a) 예산 미확정 — 미충족 |
| C2 실브라우저 실행 | **미검증** | 환경에 OPENAI/ANTHROPIC 키 없음 |
| 회귀 | 전 파일 실행 | 최종 결과는 §12 하단 갱신 |

- 매니페스트: `usage_log.py`·`test_usage_log.py` 추가 → `test_deploy_global` 기대값 122→126(파일 2 × 미러 2).
- SKILL.md·GATES.md·CHANGELOG 갱신. **전역 배포(`deploy_global.py`)는 미실행** — `~/.codex` 변경이라 사용자 확인 대상.
- 미커밋. 커밋은 사용자 지시 시.
