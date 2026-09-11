# Orca·Paseo 대비 EAM 미도입 기능

조사일: 2026-09-17. EAM 비교 기준: `e91f938d4b2fe7f73853005340e95ae2f30c404a` (0.1.0).

대상은 기존 프로젝트에서 참고했던 `stablyai/orca`, `getpaseo/paseo`다. 공식 저장소 README와 공식 기능 문서를 읽고 현재 EAM 소스와 비교했다. 상대 제품을 설치·실행해 검증한 결과는 아니다. 문서가 설명하는 현재 기능을 기준으로 하며 실험 기능은 별도 표기한다. 아래 목록은 확인한 주요 차이이며 모든 설정·UI 세부사항을 빠짐없이 나열한 목록은 아니다.

## 이미 EAM에 있는 것

CLI 직접 입력, 편집 버퍼 왕복, 여러 세션, 이름 변경, 디렉터리 그룹·정렬, 다음/이전 연결 세션 이동, 독립 PTY 지속 세션과 attach/detach, provider 원본 history/resume, 로컬 worktree 생성·선택·삭제, diff 선택과 리뷰 의견 전달, 알림·읽음/안 읽음, 계정/사용량 표시, 작업 상태·최근 요청·도구·컨텍스트 관찰, 개인 세션 메모, 임시 세션은 미도입 목록에서 제외했다. 기능이 있다고 상대 제품과 완전히 동일한 범위라는 뜻은 아니다.

비교 소스: `lisp/eam-app.el`, `eam-session-list.el`, `eam-persistent.el`, `eam-native-history.el`, `eam-usage.el`, `eam-review.el`, `eam-worktree.el`, `eam-notifications.el`, `eam-note.el`, `native/src/{session,usage,activity,transport}.rs`.

과거 task/workspace 기능은 구현 후 사용자 요청으로 제거했다. 결정은 [설계 기록](design-decisions.md)에 남긴다. 이번 비교는 현재 소스 기준이다.

## 목록

| ID | 기능 / 확인한 제품 | 구체적인 차이 | EAM 적용 판단 | 공식 근거 |
|---|---|---|---|---|
| 01 | 통합 이동 검색 / Orca | worktree·열린 탭·파일·agent 등을 같은 검색창에서 찾음. EAM 목록과 history, 파일 탐색은 별개 | 후보. Emacs completion과 결합 가능 | [Quick Open](https://www.onorca.dev/docs/model/quick-open) |
| 02 | provider·프로젝트를 넘는 대화 검색 / Orca | 전체 provider 기록을 제목·경로·브랜치·모델·대화 미리보기로 필터링, 범위·정렬·그룹 선택. EAM은 provider/디렉터리 중심 기록 선택 | 우선 후보. 전문 검색 전체를 제공한다는 주장과는 구별 | [History](https://www.onorca.dev/docs/agents/session-history) |
| 03 | 이름 있는 실행 프로필 / Paseo | provider·model·thinking·mode를 묶어 선택. EAM은 CLI 설정을 따르며 EAM 이름 있는 프로필 선택은 없음 | 우선 후보. 실행 옵션만 저장하고 프롬프트는 삽입하지 않는 범위 | [Profiles](https://paseo.sh/docs/agent-profiles) |
| 04 | 복수 계정 보관·선택 / Orca | 계정별 격리 home에서 새 CLI 실행. EAM은 현재 계정 정보 표시 중심 | 후보. 로그인 정보 수명과 격리 정책 별도 검토. 실행 중 계정을 바꾸는 기능으로 오해하지 말 것 | [Accounts](https://www.onorca.dev/docs/agents/codex-hot-swap) |
| 05 | 파일·이미지 첨부 UX / Orca | 파일 경로 전달, 이미지 미리보기·보류 첨부 등. EAM은 텍스트/diff 선택 전달 중심 | 후보. CLI 자체 이미지 기능은 사용 가능할 수 있으나 EAM 전용 첨부 선택·미리보기는 없음 | [Files](https://www.onorca.dev/docs/editing/file-explorer), [Chat](https://www.onorca.dev/docs/agents/native-chat) |
| 06 | 하위 에이전트·백그라운드 작업 트리 / Orca·Paseo | 부모 세션 밑에 자식 작업 상태·경과 시간·대화 접근 제공. EAM의 현재 도구 이름 목록보다 넓음 | 관찰 전용으로 우선 후보. 자동 worker 생성과 별개 | [Orca chat](https://www.onorca.dev/docs/agents/native-chat), [Paseo orchestration](https://paseo.sh/docs/orchestration) |
| 07 | plan·goal 진행 표시 / Orca | TodoWrite/update_plan 체크리스트, Codex goal 상태 변경 표시. EAM은 작업 상태 관찰만 제공 | 관찰 전용으로 우선 후보. 사용자 제거 요청의 task runner와 다른 기능 | [Chat](https://www.onorca.dev/docs/agents/native-chat) |
| 08 | 턴별 변경 파일 요약·인라인 diff / Orca | 응답마다 변경 파일·추가/삭제 줄과 diff 카드를 연결. EAM 리뷰 의견 전달과는 별도 | 후보. Magit/diff 버퍼로 연결하는 방식 적합 | [Chat](https://www.onorca.dev/docs/agents/native-chat) |
| 09 | AI/사람 변경 출처 표시 / Orca | 파일의 변경 범위 출처를 diff에서 표시. EAM은 누가 수정했는지 기록하지 않음 | 이후 검토. 정확한 귀속에 제약 존재 | [Attribution](https://www.onorca.dev/docs/review/attribution) |
| 10 | 구조화된 대화·질문·승인 카드 / Orca | 터미널 위 chat 및 별도 structured chat, 질문·승인 답변과 결과 표시. EAM 승인은 CLI 원래 UI에서 수행 | 범위 큼. Orca 문서상 experimental; CLI 기능 호환성 검증 필요 | [Chat](https://www.onorca.dev/docs/agents/native-chat) |
| 11 | 원격 SSH 작업 환경 / Orca | 원격 agent·파일 편집·Git·포트 포워딩·재연결. EAM 런타임은 로컬 Unix PTY 중심 | 큰 별도 기능. Emacs TRAMP만으로 EAM 원격 지속 세션이 구현되지는 않음 | [SSH](https://www.onorca.dev/docs/ssh) |
| 12 | 모바일·웹 클라이언트와 장치 연결 / Orca·Paseo | 다른 장치에서 agent 관찰·후속 입력. Paseo relay/direct host 연결 | 별도 논의로 보류(사용자 결정). EAM은 현재 로컬 Emacs 클라이언트만 제공 | [Orca](https://github.com/stablyai/orca), [Paseo](https://github.com/getpaseo/paseo) |
| 13 | 브라우저 UI 요소를 요청에 첨부 / Orca | 요소를 선택해 HTML/CSS/스크린샷을 전달 | 이후 후보. 명시적 선택으로 한정 | [Design Mode](https://www.onorca.dev/docs/browser/design-mode) |
| 14 | 브라우저·데스크톱 조작 도구 / Orca·Paseo | agent가 브라우저를 열고 클릭·폼 입력·시각 확인. Orca는 별도 computer-use도 문서화 | EAM 자체 기능은 없음. provider에 사용자가 설치한 MCP와 구별. Paseo browser는 opt-in·desktop 필요 | [Paseo browser](https://paseo.sh/docs/browser), [Orca CLI](https://www.onorca.dev/docs/cli/overview) |
| 15 | 음성 입력·대화 / Paseo | dictation과 음성 대화 모드 | 이후 검토. 기본 로컬 STT 문서에 한국어 지원 없음. 음성 대화 모드는 별도 hidden agent session을 사용하므로 추가 AI 호출 검토 필요 | [Voice](https://paseo.sh/docs/voice) |
| 16 | 추가 provider·사용자 정의 adapter / Orca·Paseo | 두 제품 모두 Claude/Codex 밖의 provider 지원. Paseo에는 ACP adapter 설정 | 후보. EAM 지원 목록은 Claude/Codex이며 임의 CLI 실행과 usage/history 완전 지원을 구분해야 함 | [Orca](https://github.com/stablyai/orca), [Paseo custom providers](https://paseo.sh/docs/custom-providers) |
| 17 | 외부 자동화용 CLI·SDK·MCP / Orca·Paseo | 외부 프로그램에서 세션 생성·입력·상태·작업 관리 | 이후 검토. EAM 내부 manager/attach 프로토콜과 공개된 안정 API는 다름 | [Orca CLI](https://www.onorca.dev/docs/cli/overview), [Paseo README](https://github.com/getpaseo/paseo) |
| 18 | 다중 agent 배포·인계·자문 / Orca·Paseo | 같은 과제의 여러 해법 비교, 계획→구현 인계, 별도 리뷰 agent 실행 | 추가 AI 사용. 명시적으로 요청한 작업만 가능하도록 별도 결정 필요 | [Orca README](https://github.com/stablyai/orca), [Paseo orchestration](https://paseo.sh/docs/orchestration) |
| 19 | 예약 작업·반복 프롬프트 / Orca·Paseo | 스케줄로 새 agent 실행; Paseo heartbeat는 기존 agent에 주기적으로 요청 전송 | 기본 도입 보류. 자동 AI 호출과 수명 정책 변경 | [Orca automations](https://www.onorca.dev/docs/cli/automations), [Paseo schedules](https://paseo.sh/docs/schedules) |
| 20 | 외부 이벤트로 agent 시작 / Paseo | GitHub·Slack·Discord의 mention 등을 Hub가 작업으로 연결 | 별도 자동화 영역. EAM에는 없음 | [Hub](https://paseo.sh/docs/hub), [Why Paseo](https://paseo.sh/docs/why) |
| 21 | worktree setup/teardown / Paseo | 생성 후 의존성 설치·환경 파일 준비, archive 때 정리 스크립트 | 현재 EAM은 생성·실행·삭제를 명시적으로 수행. 자동 실행 정책이 달라 별도 결정 필요 | [Worktrees](https://paseo.sh/docs/worktrees) |
| 22 | 개발 서버·서비스·포트 관리 / Paseo | worktree별 service 감독, 포트 할당, proxy URL 제공 | 과거 제거한 task runner와 겹침. 자동 복원 대상 아님 | [Worktrees](https://paseo.sh/docs/worktrees) |
| 23 | 유휴 세션 절전 / Orca | 완료·비활성 세션의 CLI를 정리하고 나중에 대화 ID로 재실행 | experimental·기본 off. EAM detach 지속 실행/자동 연결 금지 정책과 조율 필요. 메모는 CLI 대화별 보존 | [Hibernation](https://www.onorca.dev/docs/agents/hibernation) |
| 24 | worktree 작업판·진행 메모·고정·archive / Orca | todo/in-progress/in-review/completed와 진행 comment, pin·archive·sleep 등을 worktree 단위 관리 | 현재 EAM 개인 session note와 수명·작성 주체가 다름. workspace 복구를 전제로 하지 말 것 | [Checkpoints](https://www.onorca.dev/docs/cli/worktree-checkpoints), [Worktrees](https://www.onorca.dev/docs/model/worktrees) |
| 25 | 프로젝트별 창·탭 묶음 복원 / Orca | 편집기·터미널·브라우저를 worktree별 작업 환경으로 관리·복원 | EAM workspace 기능은 사용자 요청으로 제거. Emacs 창 분할 자체는 이미 가능 | [Orca model](https://www.onorca.dev/docs/model/worktrees) |
| 26 | 제품 전용 플러그인 체계 / Paseo | theme·panel·command·settings·provider 확장 | EAM은 Emacs Lisp 확장 체계를 활용 가능. 별도 플러그인 제품을 만들 실익 낮음. 문서는 v0.7/current와 v0.8/beta 구분 | [Paseo README](https://github.com/getpaseo/paseo) |
| 27 | AI 메타데이터 자동 생성 / Paseo | 제목·브랜치명·커밋 메시지·PR 문구를 별도 모델 호출로 생성 | 현재 EAM 원칙에 따라 기본 제외 | [Metadata generation](https://paseo.sh/docs/metadata-generation) |
| 28 | PR/CI/이슈와 세션 연결 / Orca·Paseo | PR·checks·review·issue를 작업 환경에 표시. Orca는 Linear/Jira 연동도 문서화 | 후보. 먼저 읽기 전용 정보와 명시적 연결만 검토 | [Orca integrations](https://www.onorca.dev/docs/review/github), [Paseo](https://paseo.sh/docs/why) |

## EAM에 없다고 볼 필요가 없는 편집기 기능

Orca의 파일 탐색·편집, Markdown/이미지/PDF 보기, Git stage/commit/push, 창 분할은 EAM이 자체 UI로 재구현하지 않았다. Emacs의 Dired·파일 버퍼·각 major mode·Magit·window 기능을 활용하는 영역이므로 모두 제품 결손으로 계산하지 않는다. EAM에서 AI 세션과의 연결을 더 편리하게 할 여지는 있다.

## 우선 검토 의견

이는 상대 제품의 주장과 별개인 EAM 적용 판단이다.

1. **02 통합 history 검색**: 현재 기록을 활용하며 추가 AI 호출이 없다. 전체 본문 무제한 인덱싱 대신 식별·제목·미리보기 범위부터 시작할 수 있다.
2. **03 실행 프로필**: quick/new 실행 옵션을 반복 설정하는 수고를 줄인다. 시스템 프롬프트/스킬을 넣지 않는 옵션 묶음으로 제한할 수 있다.
3. **06·07 하위 작업/plan 관찰**: 여러 agent가 실제로 무엇을 하는지 보기 쉬워진다. 읽기 전용 표시부터 검토하며 자동 위임과 분리한다.
4. **28 PR/CI 연결**: 해당 세션의 브랜치와 CI 결과를 연결하되 GitHub 쓰기나 AI 재실행은 자동으로 하지 않는다.
5. **04 복수 계정 선택**: 실제 복수 계정 사용 필요가 있을 때 유용하다. 구독 multiplier 판별 문제의 해결책은 아니다.

## 오해하기 쉬운 차이

- Orca의 `worktree checkpoints` 문서는 코드 스냅샷/롤백이 아닌 자유 텍스트 진행 메모와 카드 상태를 설명한다.
- Orca account hot-swap 문서는 새 세션의 계정을 선택하며 기존 프로세스는 재시작 전 계정을 유지한다고 설명한다. hibernation 페이지의 간략한 교차 링크 설명보다 전용 계정 문서의 제약을 따른다.
- Paseo metadata generation은 실제 별도 AI 요청이다. 현재 EAM 원칙과 맞지 않으므로 편의 기능이라는 이유로 자동 도입하지 않는다.
- Paseo orchestration MCP 도구와 browser 도구는 문서상 opt-in이다. 모든 사용자에게 기본 삽입된다고 단정하지 않는다.
- 개별 CLI가 자체적으로 제공하는 plan·subagent·이미지 입력·MCP 기능은 EAM 안에서도 CLI를 통해 사용할 수 있다. 위의 미도입은 **EAM의 별도 관리·표시·연동 기능 부재**를 뜻한다.

이번 작업은 조사 문서 작성만 수행했다. 기능 변경·설치·계정 변경·커밋·push는 하지 않았다.
