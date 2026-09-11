# 코드 구조와 native runtime

현재 버전 0.1.0. 제품 실행은 Emacs Lisp와 Rust 실행 파일 `eam-runtime`으로 구성한다. Python은 실행 의존성이 아니다. Ghostel은 별도 의존성이다.

## 모듈 책임

| 위치 | 책임 |
|---|---|
| `lisp/eam-app.el`, `eam-terminal.el` | 공개 명령·키, CLI 표시·입력, 외부 편집기·초안 |
| `lisp/eam-persistent.el`, `eam-session-list.el` | 데몬 요청, attach/detach/quit/persist, 목록·이름·정렬 |
| `lisp/eam-remote.el` | 선택적 자동 시작 모드, Tailscale 주소 조회, 기존 웹 서버 인증·재사용 |
| `lisp/eam-native.el` | 비동기 빌드·캐시·native 실행 파일 선택 |
| `lisp/eam-native-history.el`, `eam-usage.el` | 제한된 대화 페이지, 관찰 worker와 표시 캐시 |
| `lisp/eam-note.el`, `eam-notifications.el`, `eam-persistent-events.el` | 메모, 알림 수신·읽음·원본 세션 연결 |
| `lisp/eam-review.el`, `eam-worktree.el`, `eam-caffeine.el` | diff 의견, Git worktree, 유휴 잠자기 방지 |
| `native/src/session.rs`, `transport.rs` | 세션 관리, PTY 데몬·연결 클라이언트, 수명·크기·역압 |
| `native/src/serve.rs`, `serve/`, `native/web/` | 별도 `serve` 프로세스의 HTTP·WebSocket, Emacs 제어권 전환, 내장 웹 터미널 |
| `native/src/history.rs`, `usage.rs`, `activity.rs` | provider 원본 기록 조회·정확한 대화 식별·작업 상태 추정 |
| `native/src/account.rs`, `quota.rs`, `telemetry.rs` | 계정·한도, 세션별 숫자/ID callback |
| `native/src/editor.rs`, `events.rs` | 외부 편집기 연결, 터미널 알림 파싱 |
| `tests/`, `native/tests/`, `native/examples/` | 회귀 검사·컴파일된 가짜 CLI. 배포 tar에서 제외 |
| `bridge/`, `experiments/` | 과거 구현·실험. 현행 제품 실행 경로가 아님 |

## 빌드·캐시

첫 native 기능 사용 시 바이너리가 없거나 소스가 달라졌으면 `cargo build --release --locked`를 백그라운드로 실행한다. `*EAM native build*`에 로그를 표시하고 `C-c C-k`로 취소한다. 완료 후 원래 EAM 명령을 다시 실행한다. `M-x eam-native-build`로 미리 빌드할 수 있다. 설치·require·autoload·byte compile만으로 빌드하거나 AI를 호출하지 않는다.

처음에는 Rust/Cargo·시스템 C 빌드 도구와 의존성 다운로드가 필요하다. SQLite는 bundled 빌드다. Cargo는 PATH 또는 `~/.cargo/bin/cargo`에서 찾고 `eam-native-cargo-executable`로 지정할 수 있다. 컴파일러 자체를 자동 설치하지 않는다.

바이너리는 `eam-directory/native/` 아래 플랫폼·소스 해시별로 캐시한다. Cargo.toml/Cargo.lock/Rust 소스·웹 자산·파일명 변경을 감지하며 실행 중 바이너리는 덮어쓰지 않는다. `eam-native-auto-build=nil`은 자동 빌드를 끄고, `eam-native-executable`은 사전 빌드 파일을 지정한다. 캐시 실행에는 Cargo·Python이 필요 없다. [설치 예시](package-testing.md)를 참고한다.

## 프로세스·저장 책임

Emacs가 manager와 attach client를 시작하고 데몬이 CLI를 유지한다. 자세한 경로·출력 상한은 [PTY 백엔드](pty-backend.md)를 따른다. 소유 ID와 attachment PID/token으로 잘못된 프로세스·편집기 연결을 구분한다. 상태 조회·종료는 전용 Unix datagram socket을 사용하며 저장된 PID만 보고 임의 신호를 보내지 않는다.

CLI의 `EDITOR`·`VISUAL`에는 `/tmp/eam-<id>/editor`라는 단일 실행 경로를 전달한다. 세션 전용 0700 셸 런처가 native runtime과 `editor.json` 경로를 인용하고 파일 인자를 그대로 전달한다. CLI가 셸 인용을 해석하지 않고 편집기를 직접 실행해도 동작하며, 실제 설치·세션 경로의 공백과 한글도 보존한다. 런처는 현재 `editor.json`을 읽는 runtime으로 연결하므로 detach 후 다른 Emacs에 attach해도 새 endpoint를 사용한다. 이 변경은 새로 시작한 CLI부터 적용되며 기존 CLI는 detach/attach만으로 환경 변수가 바뀌지 않는다.

기본 데이터 루트는 사용 설정의 `~/.eam/`이며 `.emacs.d`와 분리한다. 주요 세션 파일은 다음과 같다.

| 파일 | 내용·수명 |
|---|---|
| `session.json` | 제공자·프로젝트·인증 루트·생성 시각·임시 여부·정상/비정상 종료 상태·명시적 resume ID |
| `closure.json`, `recovery.lock` | 명시적 종료/성공한 재개 기록과 중복 복구 방지 잠금 |
| `display.json` | 선택적 이름. 종료 상태 쓰기와 분리해 원자적 교체 |
| `events.jsonl`, `notification-read.json` | 알림 이벤트와 읽음 위치. 본문 전체를 세션 목록에 적재하지 않음 |
| `telemetry.json` | 허용된 대화 ID·경로·사용량 수치. 프롬프트 사본은 저장하지 않음 |
| `pending-note.json`, `note-binding.json`, `note.lock` | 대화 식별 전 메모·확인한 대화 키·이전/저장 잠금. 종료 후 보존 |
| `../../notes/notes.sqlite3` (세션 디렉터리 기준) | 제공자·CLI 데이터 루트·대화 ID별 메모와 충돌 검사용 revision |
| `editor.json` | 현재 연결 클라이언트의 편집기 endpoint·PID/token |
| `output.ansi` | 명시적으로 진단 기록을 켠 새 세션에서만 생성 |

소켓·launch metadata 등 데몬 임시 파일은 길이 제한을 고려한 private runtime 디렉터리에 둔다. 패키지 제거가 사용자 대화·`~/.eam/`·provider 저장소를 지우지 않는다.

## 계정·사용량·작업 관찰

표시 중인 CLI에 기본 10초 주기로 비동기 worker를 실행한다. 전역 동시 2개, 결과 16KiB, 8초 deadline이며 숨은 버퍼는 조회하지 않는다. detach/kill 시 worker·타이머를 정리하고 redisplay는 캐시만 읽는다.

- Claude statusline·Codex notify 등 세션별 실행 인수의 callback으로 ID·숫자를 얻는다. 개인 설정 파일·모델 컨텍스트는 변경하지 않고 기존 callback을 제한 시간 내 이어 실행한다.
- Codex app-server의 조회 전용 계정·한도·사용량 API만 사용한다. thread/turn을 만들지 않는다. provider 한도는 세션별 60초 캐시, 마지막 정상 값의 원본 시각과 최신 조회 시각을 구분한다.
- Claude 계정 조회는 `auth status --json`과 로컬 프로필을 사용한다. 기본 `~/.claude`와 사용자가 명시한 `CLAUDE_CONFIG_DIR`를 구분한다. 기본 경로를 강제로 환경변수로 지정하면 다른 프로필을 읽을 수 있어 `auth_root_override`를 저장한다. 옛 세션은 루트 경로에서 추론한다.
- 파일 기반 계정 fallback은 로그인 유효성을 검증한 결과가 아니다. 비밀 토큰·API 키를 표시 결과에 넣지 않으며 계정명이 없다고 0이나 임의 이름을 만들지 않는다.
- Codex token_count는 누적 값, Claude usage는 최신 주 대화 응답 기준이다. 구독 한도 비율·세션 컨텍스트·토큰 수·청구 금액을 동일시하지 않는다. `7d recorded`는 제공된 UTC 일별 수치이며 완전한 주간 청구량 보증이 아니다.
- plan 원문에 없는 5x/20x를 추정하지 않는다. `eam-account-plan-labels`는 명시적인 사용자 표시명이다.
- transcript 앞/뒤 각 1MiB 내에서 현재 요청·도구·상태를 관찰한다. 범위 밖 값은 미확인이다. 목록은 비싼 계정 RPC/lsof 없이 이미 식별한 기록만 조회한다.

상단 tab-line에는 계정·plan·계정 한도, header-line에는 세션 수치·작업 상태를 표시한다. 사용자가 가진 tab-line 설정은 해제 시 복원한다. [세션 관찰](session-observation-validation.md)에서 추정 상태와 메모·quick 수명을 설명한다.

## 검증 경계

현행 실행·설치 검사와 과거 측정은 [검증 요약](validation.md)에 모은다. 구버전 Python 성능 수치를 Rust의 성능으로 사용하지 않는다. 실행 중인 옛 데몬에는 코드 재로드만으로 프로토콜 변경이 적용되지 않으며, 필요한 경우 정상 종료 후 새 세션 또는 저장 대화 재개로 전환한다.
