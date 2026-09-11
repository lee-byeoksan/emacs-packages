# EAM 설계 결정

2026-09-17 정리. 과거 구현 일지에서 현재 제품에 영향을 주는 선택과 반례를 추렸다. 명령·키의 현행 기준은 [사용 안내](user-guide.md), 코드 책임은 [native runtime](native-runtime.md)이다.

| 결정 | 이유와 포기한 대안 | 제약·재검토 조건 |
|---|---|---|
| 공식 Claude·Codex CLI를 Ghostel에서 실행 | 초기 일반 버퍼/JSON 어댑터만으로 `/model`, `/goal`, 승인·파일 수정 등 CLI 전체 동작을 대체하기 어려웠음 | CLI UI·버전 변화에 영향을 받음. EAM이 승인 UI를 독자적으로 복제하지 않음 |
| 터미널 직접 입력이 기본, Emacs 편집 버퍼는 선택적 | 일반 버퍼의 한글 편집·undo를 활용하면서 CLI 입력 흐름 유지 | CLI 외부 편집기 왕복과 새 초안 붙여넣기를 구분. 붙여넣기와 제출은 별도 |
| Ghostel은 원본 의존성으로 사용 | Orca·Paseo·기존 Emacs 패키지는 개념을 참고하되 포크하지 않음 | vterm 대비 성능 우위를 측정한 것으로 주장하지 않음 |
| 독립 Rust PTY 데몬 | Emacs를 껐다 켜도 같은 CLI 프로세스를 유지. 사용자가 직접 Ghostel보다 tmux 연결의 스크롤이 느리다고 관찰해 PTY로 전환, 이후 tmux 경로 삭제 | 현행 연결은 세션당 클라이언트 하나. 모든 지연이 tmux 때문이었다는 일반적 벤치마크 결론은 아님 |
| 원격 웹은 같은 Rust 바이너리의 별도 `serve` 프로세스 | 기존 manager·PTY 프로토콜을 재사용하고 웹 UI를 내장해 Python·npm 실행 의존성을 제거. 웹 서버 교체와 CLI 수명을 분리 | 제어권은 단일 연결 간 전환. 원래 Emacs로 자동 반환하되 강제 종료 시 반환 정보는 소실. [운영 방법과 한계](remote-access.md) |
| 실행 중 Python 의존성 제거 | 각 머신에서 native 바이너리를 빌드·캐시. 데몬·attach·history·알림·외부 편집기까지 Rust로 통합 | 최초 빌드에는 Rust/Cargo·C 도구 필요. 과거 Python 실험 코드는 현재 실행 경로가 아님 |
| 자동 빌드는 백그라운드, 소스 해시별 캐시 | Emacs 정지 없이 로그 확인·취소 가능. 실행 중인 데몬 바이너리를 덮어쓰지 않음 | 구버전 데몬은 새 프로토콜/수명 정책을 자동 취득하지 않음 |
| 일반 세션은 지속, quick만 연결 해제 시 종료 | 장기 작업과 간단한 질문의 서로 다른 수명 요구 | `eam-persist`는 데몬 확인 후 quick을 지속으로 전환. quick 연결 실패는 30초 유예 후 종료 |
| 대화 원본은 provider가 소유 | 대화 사본 저장을 줄이고 CLI 원본에서 history/resume 제공 | 내부 JSONL/SQLite 형식은 안정 API로 가정하지 않음. 원시 터미널 기록은 진단용 opt-in |
| 현재 대화는 ID·콜백·소유 프로세스의 열린 파일로 식별 | 같은 디렉터리의 최근 대화를 현재 세션으로 잘못 연결하지 않기 위함 | 식별 불가 시 안내. `C-c a h`는 직접 조회, `C-c a o`만 수동 선택 |
| 출력·조회는 제한, 편집 undo는 유지 | 긴 로그·다중 세션에서 출력 버퍼가 무제한 증가하지 않도록 함 | 버퍼 상한은 전체 RSS 상한이 아님. 입력 undo·네이티브 할당은 별도 |
| 프롬프트·스킬·컨텍스트 자동 삽입과 불필요한 모델 호출 금지 | CLI의 기존 구독 로그인과 사용자의 직접 요청을 존중 | 알림·숫자 수집 callback 및 읽기 전용 계정 조회는 별도. 사용자 CLI 자체 훅까지 무호출이라고 보장하지 않음 |
| 개인정보 설정은 수정하지 않고 세션별 실행 인수 사용 | 별도 로드·패키지 제거가 개인 Emacs/CLI 설정에 영향을 주지 않게 함 | `~/.eam/`의 데이터·캐시는 패키지와 독립. CLI 원본 기록도 별도 |
| 이름·읽음 상태·노트는 분리 저장 | 데몬 종료 메타데이터 쓰기와 사용자 수정이 서로 덮어쓰지 않게 함 | 노트는 대화 ID별 SQLite 저장·revision으로 동시 수정 충돌 감지. 종료해도 보존 |
| M-x와 키 중심, provider는 실행 시 자동완성 | 제공자별 명령 중복·GUI 메뉴·시작 화면의 복잡성 축소 | 새 키를 무조건 추가하지 않음. task/workspace 기능은 사용자 요청으로 제거 |
| caffeine은 attach 수명에 따라 자동 활성화 | 로컬·웹 attach가 각각 assertion을 소유하며 수동 활성화는 별도 유지 | 덮개 닫기·강제 잠자기·전원 상실 방지는 아님 |

## 종료 경계와 채택하지 않은 감시자

초기 실제 CLI와 가짜 프로세스 계층 시험에서 CLI 강제 종료 뒤 작업 자식이 남는 반례가 있었다. 같은 프로세스 그룹에 신호를 보내는 것만으로 `setsid`로 분리된 작업까지 소유할 수 없다.

외부 watchdog을 50ms 간격으로 PID·부모 PID·시작 시각을 추적하는 방식도 시험했다. 이미 관찰한 작업은 정리했지만, 관찰 공백 중 double-fork/setsid 후 부모가 종료된 작업을 놓쳤다. **관찰한 잔여 작업 0개가 전체 잔여 작업 0개를 뜻하지 않는다.** 조회 주기 단축이나 launchd 도입만으로 해결된다고 판단하지 않아 완전한 작업 정리 기능으로 채택하지 않았다. 실험 소스는 `experiments/lifecycle/`에 남으며 제품 기본 경로가 아니다.

현재 데몬은 자신이 실행한 CLI의 종료를 기준으로 정리한다. helper가 PTY slave를 유지하더라도 CLI 종료 후 제한 시간 안에 세션을 종료한다. 외부의 임의 작업을 완전히 추적·정리한다고 보장하지 않는다.

## 보류·미확정

- 모바일 CLI 제어: 독립 데몬을 활용할 가능성은 확인했지만 사용자 요청으로 별도 논의에 둔다. 네트워크 연결·인증·다중 표시/입력·화면 크기·재연결 정책은 결정하지 않았다.
- Claude 스크롤: [실제 측정](claude-scroll-validation.md)과 terminal 환경 전달 수정은 확보했지만, 수정 후 GUI 체감 개선은 아직 확정하지 않았다.
- 전체 TUI snapshot 복원, 수일 GUI 메모리 안정성, 비텍스트 첨부의 완전한 편집 왕복, 임의 하위 프로세스 소유권은 보장 범위 밖이다.
- 기능 조사 자료의 제안은 자동으로 개발 범위에 넣지 않는다. 이미 삭제한 task/workspace를 재도입하려면 별도 요구가 필요하다.


## 점자 애니메이션의 macOS 폰트 호환 처리

2026-09-17 조사에서 일반 Ghostty는 정상, Ghostel 단독과 EAM 모두 Codex 입력창 효과가
깨진다는 사용자 관찰이 있었다. 실제 GUI 속성 조회에서 효과가 U+2800–U+28FF 문자이고
Apple Braille로 표시되며 Ghostel이 일부 문자를 두 칸으로 확장함을 확인했다.
12px 글리프 측정에서 Apple Braille의 U+2800은 빈 패턴인데도 ink bounds가 있었고,
Apple Symbols의 U+2800은 ink가 없었다. 일반 문자가 정상이라는 이유로 폰트 문제를
배제할 수 없었다.

Ghostel 의존성을 포크·수정하지 않고 `eam-terminal-display.el`에서 성공한 native redraw의
repainted 범위만 보정한다. EAM 소유 버퍼의 점자 face에만 family를 지정하고 색상은
보존한다. 점자 글리프를 한 칸으로 맞추며 Ghostel이 차용해 숨긴 다음 공백도 복구한다.
macOS 기본 Apple Symbols, nil로 해제 가능, 미설치 폰트·비GUI는 적용하지 않는다.
후처리이므로 Ghostel의 원래 폰트 측정 비용까지 줄이지는 않는다.

검증: 실제 Ghostel 모듈에 200개 RGB/배경색 프레임을 공급하고 여러 프레임을 묶어
갱신한 후 reset/erase했을 때 색상 잔류가 없었다. GUI ERT로 실제 Apple Symbols 선택,
ANSI 색상·한글 속성 보존, 차용 공백 복구를 확인했다. Computer Use 전후 화면에서
입자 주변의 점자 틀이 사라지고 공백 복구 후 입력창 배경 폭이 유지됨을 확인했다.
이는 합성 색상 검사와 실제 화면 관찰 결과이며 Codex의 모든 효과·테마·폰트 크기에 대한
검증은 아니다. 일반 Ghostel 버퍼에는 적용하지 않는다.


## 노트의 소유자는 EAM 실행이 아닌 CLI 대화

EAM 실행 세션은 PTY/데몬 수명이고 CLI 대화는 provider의 resume 가능한 대화 ID다.
메모는 `(provider, auth_root, conversation_id)` 키로 SQLite에 보존한다. 디렉터리·최근
대화 순서로 대상을 추정하지 않는다. 아직 ID가 없으면 pending-note.json에 보관한다.
기존 telemetry 및 수동/주기적 observation에서 확인한 ID로 이전하고 note-binding.json을
남긴다. 다른 내용이 이미 있으면 pending을 삭제하거나 덮어쓰지 않는다. 구 note.json은
같은 잠금 아래 pending으로 이전하므로 구 데몬의 종료 삭제로부터 분리된다.

노트 편집기는 최초 대화 키를 유지한다. 저장 시 revision 비교로 동시 수정 충돌을
거부하며 미저장 버퍼를 유지한다. CLI 종료를 이유로 버퍼를 강제 삭제하던 타이머는
제거했다. 임시 quick CLI에도 같은 보존 정책을 적용한다. CLI 데이터 루트를 옮기면
다른 키로 취급하며, 기존에 이미 삭제된 노트는 복구할 수 없다.


## 비정상 종료한 실행의 복구 목록

EAM 실행 상태에 `interrupted`를 추가한다. 임시 runtime 디렉터리가 없어지거나 control
요청 실패와 함께 daemon lifetime lock이 해제됐음을 확인한 경우 복구 대상으로 본다.
잠금이 살아 있는 제어 응답 지연은 `unavailable`/unverified로 유지한다. metadata 읽기는
runtime이 사라져도 가능하게 하되 존재하는 runtime의 소유권 검증은 유지한다.
명시적 quit은 closure.json에 별도 보존하고, 새 데몬은 OS 종료 신호·전송 오류·CLI 비정상
종료와 정상 exit/quick detach를 구분한다. 구버전에서 이미 stopped로 남긴 항목은 추정하지 않는다.

기존 list-live 프로토콜은 기본적으로 살아 있는 세션만 반환한다. 목록 UI만
include_interrupted를 요청하며, next/previous 순환과 detached 필터는 자동 재개하지 않는다.
RET 복구는 provider 프로필·디렉터리·이름과 확인된 UUID를 사용한다. UUID가 없으면 native
resume picker를 열고, 최근 파일이나 cwd로 대화를 추정하지 않는다. recovery.lock으로
같은 원본의 동시 재개를 막고, 새 daemon 시작 확인 후에만 closure에 replacement를 남긴다.
새 daemon 시작 후 Emacs attach 실패 시에도 새 항목은 detached로 재접근할 수 있다.

복구 회귀 검증: 가짜 CLI의 데몬 SIGKILL 후 runtime 삭제, CLI SIGKILL, 데몬 SIGTERM,
SIGSTOP에 의한 제어 응답 지연을 구분했다. 목록 잔존·UUID 보존·명시적 제외·실패 후 재시도·
성공 후 원본 제외·중복 재개 거부를 확인했다. Rust 40개 및 ERT 88개 통과,
기존 점자 폰트 GUI 전용 1개는 배치에서 제외했다. 실제 전원 차단이나 실제 provider의
resume 화면은 이 검증에서 실행하지 않았다.
