# 원격 터미널 접속

한 대의 PC에서 실행 중인 지속 세션을 Android·다른 PC의 브라우저에서 선택하고 조작한다. 폴더 탐색·생성 후 Claude 또는 Codex 세션을 새로 시작할 수도 있다. CLI 화면을 그대로 전달하므로 승인 요청도 터미널에서 응답한다.

## 실행

Emacs에서 자동으로 시작하려면 EAM의 `use-package` `:config`에 다음을 추가한다:

```elisp
(require 'eam-remote)
(eam-remote-mode 1)
```

이 설정을 로드하면 Tailscale CLI에서 자신의 HTTPS 주소를 조회하고 서버를 시작한다. 같은 주소·인증 계정·세션 경로·탐색 루트의 서버가 있으면 재사용한다. 다른 설정의 서버가 포트를 사용하면 상태 메시지를 남긴다. Emacs 종료·모드 해제는 실행 중인 서버를 종료하지 않는다. native 빌드가 필요하면 완료를 기다렸다가 시작한다. 일반 `require`나 batch 패키지 검사만으로 서버를 시작하지 않는다.

기본 인증은 Tailscale이다. PC 기기의 소유 계정을 조회해 그 계정만 허용하므로 모바일에서는 같은 계정으로 Tailscale에 연결하고 주소를 열면 된다. 별도 토큰 입력이나 로그인 쿠키가 필요 없다. 다른 계정·신원 없는 요청·기존 토큰은 거부한다. 소유 계정을 조회할 수 없거나 태그 기기이면 자동 시작을 중단하며 토큰 인증으로 자동 전환하지 않는다.

`M-x eam-remote-info`에서 주소·허용 계정·로그 위치를 확인한다. Tailscale이 아직 연결되지 않았다면 연결 후 `M-x eam-remote-start`를 실행한다. 자동 재시작 감시자는 아니므로 서버를 별도로 종료한 경우에도 이 명령으로 다시 시작한다.

`M-x eam-remote-restart`는 인증과 설정을 확인한 기존 서버에 정상 종료를 요청하고 새 서버를 시작한다. 웹 연결의 제어권을 원래 Emacs로 반환하고 CLI 프로세스는 유지한다. 브라우저는 재시작 후 새로고침한다. 다른 설정이나 계정의 서버는 자동 종료하지 않는다.

`eam-remote-origin`의 기본값 nil은 주소 자동 조회다. 로컬만 사용하려면 `eam-remote-auth`를 `token`으로 설정하고 `eam-remote-origin`을 `http://127.0.0.1:8765`처럼 지정한다. `eam-remote-port`, `eam-remote-browse-root`, `eam-remote-tailscale-executable`을 설정할 수 있다. 세션 경로는 Emacs의 `eam-directory/persistent`를 사용한다. 아래의 Tailscale Serve 연결은 최초 한 번 별도로 설정해야 한다.

### 직접 실행

저장소 루트에서 빌드하고 실행한다. 설치 패키지 사용자는 `M-x eam-native-build`로 만든 캐시의 `eam-runtime`으로 같은 `serve` 명령을 실행할 수 있다.

```sh
cargo build --release --locked --manifest-path eam/native/Cargo.toml
eam/native/target/release/eam-runtime serve
```

기본 주소는 `http://127.0.0.1:8765`다. 터미널에 출력된 접속 토큰으로 로그인한다. `EAM_WEB_TOKEN` 환경 변수로 고정 토큰을 지정할 수 있다. 허용 문자는 영문·숫자·밑줄·하이픈이다.

`--token-file /path/to/token`은 토큰을 생성·보관하고 다음 실행에서 재사용한다. 부모 디렉터리는 미리 만들어야 한다. 저장된 토큰과 `EAM_WEB_TOKEN`이 다르면 오류로 중단한다. Emacs의 명시적 token 모드만 이 옵션을 사용한다.

PC와 모바일을 같은 Tailscale 네트워크에 연결하고 PC에서 실행한다:

```sh
tailscale serve --bg http://127.0.0.1:8765
```

로컬 웹 서버를 Ctrl-C로 종료한 다음, Tailscale이 알려준 HTTPS 주소를 정확히 지정해 다시 실행한다:

```sh
eam/native/target/release/eam-runtime serve --origin https://YOUR-HOST.ts.net \
  --tailscale-user YOUR-LOGIN
```

브라우저에서도 이 HTTPS 주소로 접속한다. 서버는 loopback에만 바인딩하며 지정한 Host·Origin을 확인한다. `--tailscale-user`는 허용할 로그인 이름(예: 본인 이메일)을 정확히 지정한다. 이 모드는 `EAM_WEB_TOKEN`과 기존 쿠키를 인증에 사용하지 않는다. 명시적 token 모드의 쿠키만 HttpOnly·SameSite=Strict·HTTPS Secure를 사용한다. Tailscale Serve 설정은 웹 서버 재시작과 별개로 유지된다.

기본 세션 경로는 `~/.eam/persistent`, 탐색 루트는 홈 디렉터리다. `--sessions /path/to/eam/persistent`, `--browse-root /path/to/projects`, `--port 8765`로 변경한다. CLI는 서버의 PATH에서 찾고 PC의 기존 로그인을 사용한다. 웹 서버 실행에는 Python·npm이 필요하지 않다.

Tailscale Serve가 클라이언트의 신원 헤더를 제거한 뒤 넣어 주는 `Tailscale-User-Login`을 신뢰한다. 서버는 loopback에만 바인딩하고 같은 PC의 로컬 프로세스는 신뢰한다. Emacs의 상태 확인과 재시작도 로컬에서 이 헤더를 사용한다. 다른 프록시로 직접 공개하면 이 신뢰 조건이 성립하지 않는다. [Tailscale Serve 신원 헤더](https://tailscale.com/docs/features/tailscale-serve)

## 접속 주소 이름

기기 이름은 Tailscale 관리 화면에서 `eam`처럼 지정할 수 있다. 주소는 `eam.<tailnet>.ts.net`이 된다. tailnet DNS 이름은 임의 문자열 입력 대신 Tailscale이 무작위로 제안하는 이름 중 선택한다. [기기 이름](https://tailscale.com/docs/concepts/machine-names), [tailnet 이름](https://tailscale.com/docs/concepts/tailnet-name)을 참고한다.

Emacs 자동 시작은 매번 현재 주소를 조회하지만 실행 중 서버의 주소를 바꾸지는 않는다. 이름 변경 후에는 기존 웹 서버를 정상 종료하고 Tailscale Serve 설정을 확인한 뒤 `eam-remote-start`로 재시작한다. 직접 `serve`를 실행할 때는 `--origin`에 해당 주소를 전달해야 한다.

## 제어권 전환

1. Emacs에서 세션을 열어 둔 상태로 웹의 **조작권 가져오기**를 누른다. CLI는 유지하고 Emacs의 attach 연결만 해제한다.
2. 터미널에 입력하거나 보조 입력창의 **터미널에 넣기**로 붙여넣는다. 제출은 Enter로 구분한다. 승인은 CLI 화면에 따라 키로 응답한다.
3. **PC로 돌려주기**를 누르면 기존 Emacs 버퍼와 초안을 재사용해 연결한다. 탭 종료·연결 단절 감지 시에도 자동으로 반환한다. 조용한 네트워크 단절은 약 30초의 heartbeat 제한과 감지 주기만큼 시간이 걸릴 수 있다.

다른 웹 연결로 제어권을 넘길 때는 중간에 Emacs로 반환하지 않는다. 웹에서 새로 시작한 세션에는 이전 Emacs 연결이 없으므로 웹 해제 후 PC의 `eam-attach`로 연결한다. quick 세션은 먼저 `eam-persist`한다.

## 모바일 화면과 조작

스크롤바는 오른쪽 출력 영역 위에 겹쳐 표시하며 별도 열 너비를 차지하지 않는다. 터미널 화면은 현재 뷰포트 높이에 맞추고, 세션 목록과 폴더 목록만 내부에서 스크롤한다. 상단은 세션 이름과 연결 표시등·아이콘 버튼을 한 줄에 배치한다. 연결은 초록, 연결 끊김·연결 중·응답 지연은 빨강으로 표시한다. 표시등 옆에는 터미널 WebSocket 왕복 응답시간을 ms로 표시하고 연결이 끊기거나 지연되면 수치를 지운다. 아래 조작 키는 두 줄로 배치하며 C-c, C-d, S-tab, 네 방향 화살표와 Enter를 제공한다. 설명 문구와 세션 ID는 일반 화면에 표시하지 않는다. 키보드 표시·회전·전체화면 전환 시 터미널 크기를 다시 맞춘다.

- 터미널을 손가락으로 위아래로 쓸거나 **▲ / ▼** 버튼으로 출력 기록을 이동한다. **이중 ▼**는 맨 아래로 이동한다. 화살표 키는 CLI로 전달하는 입력이다.
- **버퍼 / 라이브** 토글로 입력 모드를 선택한다. 기본 버퍼 모드는 터미널을 짧게 탭·클릭하면 입력 모달이 열린다. 모드 전환만으로 키보드를 띄우지 않으며, 라이브 모드도 터미널을 눌러 직접 입력을 시작한다. 선택은 브라우저에 저장하고 전환해도 버퍼 초안은 유지한다. 스와이프·드래그는 모달을 열지 않는다. 현재 커서가 있는 줄(자동 줄바꿈 포함) 또는 인식된 여러 줄 입력 영역을 눌렀을 때만 입력을 시작하고 이전 입력·출력 클릭은 무시한다. PTY에는 입력칸 의미 정보가 없으므로 인식할 수 없는 화면에서는 커서 줄만 사용한다. **터미널에 넣기**는 붙여넣고 모달을 닫으며, **Enter**로 별도 실행한다. 닫기·Esc로 닫으면 초안은 남는다.
- 상단 아이콘으로 **테마 전환**, **전체화면**, **연결 해제**, **재연결**, **세션 목록**을 조작한다. 라이트·다크 모드 선택은 브라우저에 저장하며 터미널에도 적용한다. ANSI 16색 팔레트도 전환하며 최소 글자 대비를 밝은 테마 7:1, 어두운 테마 4.5:1로 보정한다. CLI의 고정 RGB·256색도 렌더러의 대비 보정 대상이다. 일반 UI 텍스트와 터미널은 공통 13px 기준을 사용한다. 연결 해제는 현재 화면에 남고, 목록 버튼은 제어권을 반환한 뒤 목록으로 이동한다. **전체화면**으로 진입·해제한다. 브라우저가 해당 API를 지원해야 한다.
- 연결이 끊기면 **재연결**이 나타난다. 같은 세션의 조작권을 다시 가져오며 작성 중인 초안은 유지한다. CLI가 이미 종료되었으면 목록으로 돌아가 다른 세션을 선택한다.

브라우저는 최대 10,000줄의 로컬 스크롤 기록을 보관한다. CLI가 대체 화면을 쓰거나 출력을 지우는 경우 과거 화면 전체를 재구성하지는 않는다. 재연결 시에는 데몬의 제한된 PTY 재생을 사용하며 이전 브라우저 스크롤 위치까지 복구하지 않는다.

## 구조와 한계

```text
브라우저의 xterm.js
    ↕ HTTPS / WebSocket (Tailscale Serve)
eam-runtime serve — HTTP·인증·폴더·세션 관리·제어권 전환
    ↕ Unix socket / attach.lock
세션별 eam-runtime daemon — PTY와 CLI 프로세스 유지
```

웹 서버와 데몬은 같은 Rust 실행 파일을 사용하지만 별도 프로세스다. Axum/Tokio 서버가 기존 manager 함수를 호출하고 PTY의 바이트를 WebSocket으로 전달한다. 화면·xterm.js 파일은 바이너리에 포함된다. Emacs endpoint의 세션 경로·PID·attachment token을 확인한 뒤 해당 표시 연결만 해제한다. 잠금으로 동시 입력을 차단하고 출력 ACK로 느린 브라우저의 버퍼 증가를 제한한다.

정상 종료(SIGTERM·Ctrl-C)는 연결을 해제하고 Emacs에 반환한다. 반환 대상은 서버 메모리에 있으므로 SIGKILL·PC 전원 종료 뒤 자동 반환은 보장하지 않는다. 이 경우 PC에서 `eam-attach`한다. 자동 반환에는 기존 Emacs와 제어 소켓이 살아 있어야 한다.

PTY 재생은 최대 5MiB의 출력이며 완전한 화면 snapshot이 아니다. CLI가 Emacs 외부 편집기를 여는 동작까지 모바일 UI로 대체하지 않는다. 브라우저 구현이 네이티브 앱보다 빠르다는 성능 비교는 수행하지 않았다.

## 검증

```sh
cargo build --locked --manifest-path eam/native/Cargo.toml --bins --examples
cargo test --locked --manifest-path eam/native/Cargo.toml -- --test-threads=1
python3 -m venv eam/var/remote-web-venv
eam/var/remote-web-venv/bin/pip install aiohttp==3.13.3
eam/var/remote-web-venv/bin/python eam/tests/remote-web-test.py
python3 eam/tests/remote-start-test.py
EAM_WEB_AUTH=tailscale python3 eam/tests/remote-start-test.py
eam/var/remote-web-venv/bin/python eam/tests/remote-auth-test.py
EAM_WEB_SHUTDOWN=http eam/var/remote-web-venv/bin/python eam/tests/remote-web-test.py
EAM_WEB_BROWSER_TEST=1 EAM_WEB_LAYOUT_TEST=1 eam/var/remote-web-venv/bin/python eam/tests/remote-web-test.py
```

Python은 통합 테스트 클라이언트에만 필요하다. 테스트는 별도 임시 세션과 Emacs 소켓을 사용해 인증·경로 제한·한글·크기 변경·승인 입력·대량 출력·제어권 전환·연결 단절·정상 종료 시 CLI PID 보존을 확인한다. macOS의 `/Applications/Emacs.app`을 사용한다. `EAM_WEB_BROWSER_TEST=1`을 추가하면 agent-browser를 통한 실제 웹 버튼 왕복도 검사한다. 프로토타입은 `experiments/remote-web/`에 참고용으로 보관한다.

터치 스크롤은 프레임 단위로 이동을 모아 처리하고 손을 떼면 감속하며 이어진다. 새 터치나 스크롤 버튼은 관성을 중단한다. 라이브 모드에서는 CLI의 커서 숨김 설정과 별도로 현재 커서 위치를 가는 세로선으로 표시한다.

앱 복귀·네트워크 복구 시 기존 WebSocket에 ping을 보내 4초 안에 응답하는지 확인한다. 응답이 없으면 연결을 닫고 같은 세션으로 자동 재연결한다. 앱 복귀 시 이미 끊어진 연결도 자동 복구하며 실패하면 최대 3회 간격을 두고 재시도한다. 직접 연결 해제나 목록 이동을 선택하면 자동 재연결하지 않는다. 키보드로 가용 높이가 줄면 상단 테마·전체화면 버튼을 숨기고 현재 입력 위치를 보이게 맞춘다. 여러 줄 입력은 현재 커서 위아래의 테두리와 내부 프롬프트(❯, ›, >)가 확인되는 경우에만 전체 내부 영역을 허용한다. 인식할 수 없는 CLI 화면은 커서 줄 판별로 유지한다.

출력 선택·복사 버튼은 현재 브라우저에 남아 있는 터미널 기록의 텍스트 스냅샷을 읽기 전용 창으로 연다. 길게 눌러 선택하거나 선택 복사·전체 복사를 사용할 수 있다. ANSI 색상은 제외하고 자동 줄바꿈은 이어 붙인다. 초안은 저장하지 않는다.

브라우저 회귀 테스트는 대량 출력 수신 바이트, xterm 쓰기 콜백까지의 누적 시간, 프레임 간격의 95백분위 및 최대값, WebSocket ready까지의 시간과 takeover 시작부터 초기 ready 메시지 처리까지의 연결 시간을 출력한다. 쓰기 시간은 비동기 큐 대기가 포함되어 CPU 시간과 같지 않으며, 테스트 PC의 수치는 휴대폰이나 Tailscale 왕복 시간을 대표하지 않는다. 커서 위치와 크기 측정은 프레임당 한 번으로 묶는다.

2026-09-19 로컬 headless Chromium 측정: 약 1.34MB burst 수신 중 프레임 간격 p95 16.7ms, 최대 16.8ms. 같은 기록 재연결 시 takeover 요청 시작부터 초기 ready 메시지 처리까지 12.4ms, 재수신량 약 1.34MB. ready는 재생 시작 전에 전달되므로 이 수치는 전체 화면 복원 시간이 아니다(기존 진단 필드명 restoreMs). 실제 Android·Tailscale 환경 수치도 아니며 화면 표시 지연과 네트워크 전송량을 함께 확인해야 한다. 현재 측정만으로 snapshot 프로토콜 변경의 필요성이 입증되지는 않았다.

웹 터미널 접속 후 첫 크기 동기화에서는 PTY 행 수를 잠깐 한 줄 바꾼 뒤 100ms 후 원래 크기로 복구한다. 같은 크기로 재접속하거나 5MiB 재생 한도를 넘긴 세션에서도 CLI에 화면 재그리기를 요청하기 위한 처리다. CLI 프로세스와 입력은 유지되며, 오래된 스크롤백을 복원하는 기능은 아니다. 크기 변경에 반응하지 않는 프로그램의 화면 복원까지 보장하지는 않는다.

웹 attach 중에는 세션 목록에 `remote` 상태가 표시된다. 서버가 가진 `remote.lock` 잠금과 실제 attach 수를 함께 확인하므로 연결 해제나 서버 종료 후 남은 파일만으로 remote로 표시하지 않는다.

macOS에서는 로컬·웹 attach마다 `caffeinate -d -i`로 자동 화면/시스템 유휴 잠자기를 막고 detach 시 해당 assertion을 해제한다. attach가 여러 개면 마지막 연결이 끝날 때까지 유지된다. 웹 연결은 Emacs 종료와 무관하게 유지하며, assertion 소유 프로세스가 종료되어도 자동 해제된다. Emacs 카페인 모드 표시도 5초 간격으로 연결 상태를 따르고, 직접 켠 모드는 마지막 detach 후에도 유지한다. macOS 이외에서는 자동 assertion을 생성하지 않는다.
