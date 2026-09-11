# EAM Remote 웹 프로토타입

현재 제품 구현은 Rust `eam-runtime serve`로 이전했다. 실행 방법은 [원격 접속 안내](../../docs/remote-access.md)를 따른다. 아래는 이전 Python 실험의 재현 방법이다.

한 대의 PC에서 실행 중인 EAM Rust PTY 세션을 브라우저로 연결한다. 제품 경로를 수정하지 않은 실험이다. Python aiohttp는 이 실험에만 필요하다.

## 실행

저장소 루트에서:

```sh
cargo build --manifest-path eam/native/Cargo.toml
python3 -m venv eam/var/remote-web-venv
eam/var/remote-web-venv/bin/pip install -r eam/experiments/remote-web/requirements.txt
npm ci --prefix eam/experiments/remote-web
eam/var/remote-web-venv/bin/python eam/experiments/remote-web/server.py
```

`http://127.0.0.1:8765`에 접속해 서버가 출력한 접속 토큰을 입력한다. 토큰은 HttpOnly / SameSite=Strict 쿠키로 저장한다. HTTPS에서는 Secure 쿠키를 사용한다. 외부 CDN을 사용하지 않는다.

기본 세션 디렉터리는 `~/.eam/persistent`, 탐색 루트는 홈 디렉터리다. EAM의 `eam-directory`를 다르게 설정했다면 `--sessions /path/to/eam/persistent`를 지정한다. `--browse-root`로 탐색·생성 범위를 제한할 수 있다. CLI는 서버의 PATH에서 찾으며 기존 PC 로그인을 사용한다. API 키나 승인 우회 옵션을 추가하지 않는다.

## PC ↔ 웹 전환

1. PC 세션을 열어 둔 채 웹에서 목록을 새로고침한다. quick 세션은 먼저 `eam-persist`해야 한다.
2. 사용 중인 세션의 **조작권 가져오기**를 누른다. 저장된 Emacs endpoint의 세션 경로·PID·연결 토큰을 대조한 뒤 해당 attach 프로세스만 해제한다. CLI와 PC의 초안·버퍼는 유지한다. 같은 `attach.lock`으로 동시 입력을 차단한다.
3. 웹의 **PC로 돌려주기**를 누르면 서버가 PTY 소켓과 잠금을 해제하고, 연결 해제 확인 후 원래 Emacs에 재연결한다. 웹에서 새로 만든 세션은 반환할 Emacs 연결이 없으므로 해제 후 PC에서 `eam-attach`한다. 탭 종료·통신 단절 감지 시에도 원래 Emacs로 자동 복귀한다. 조용한 네트워크 단절은 heartbeat 감지까지 시간이 걸린다. 다른 웹 클라이언트로 조작권을 넘기는 도중에는 자동 복귀하지 않는다. 정상 서버 종료 시에도 반환하며, 강제 종료(SIGKILL)·PC 전원 종료는 보장하지 않는다.
4. 새 세션은 폴더 탐색 → 필요하면 폴더 생성 → provider 선택 → 시작한다. 첫 요청은 연결된 터미널에서 입력한다.

'터미널에 넣기'는 붙여넣기만 한다. 실행은 Enter 버튼으로 구분한다. 승인은 실제 CLI 화면을 읽고 키로 응답한다. Ctrl-C는 CLI 작업을 중단할 수 있다.

## Tailscale 연결

PC와 Android가 같은 tailnet에 연결되어 있다는 전제다. PC에서 설치된 Tailscale CLI로:

```sh
tailscale serve --bg http://127.0.0.1:8765
```

Serve가 알려준 HTTPS 주소를 정확히 `--origin`에 지정해 서버를 실행한다:

```sh
eam/var/remote-web-venv/bin/python eam/experiments/remote-web/server.py \
  --origin https://YOUR-PC.YOUR-TAILNET.ts.net
```

Android의 Tailscale을 켜고 해당 주소로 접속한다. 서버는 계속 루프백에만 바인딩한다. 지정된 origin만 허용하므로 이 실행에서는 localhost 주소로 로그인할 수 없다. Funnel은 사용하지 않는다. Tailscale 설치·설정은 이 실험에서 자동 변경하지 않았다.

## 검증

```sh
eam/var/remote-web-venv/bin/python eam/experiments/remote-web/test_gateway.py
```

격리된 임시 세션과 가짜 CLI로 인증·Origin·탐색 범위·폴더 생성·새 세션·배타 잠금·한글 바이트 왕복·크기 변경·승인 입력·1MB 이상 출력·재연결을 확인한다. 실제 AI 호출은 없다.

가짜 CLI를 화면에서 확인하려면 `--demo --sessions /tmp/eam-web-demo/sessions --browse-root /tmp`를 추가한다. Demo provider의 명령: `approval`, `size`, `burst`, `exit`. `burst`는 12,000줄을 출력한다.

## 성능 설계와 한계

- 화면 이미지 대신 터미널 바이트를 WebSocket으로 전달하고 xterm.js가 그린다.
- xterm의 write callback 이후 ACK를 전송한다. 미처리 출력 128KiB에서 읽기를 멈춰 데몬까지 역압을 전달한다. 느린 화면은 CLI 출력 속도에도 영향을 줄 수 있다.
- ACK가 30초 동안 오지 않으면 연결을 해제한다. heartbeat는 단절 탐지를 돕는다. 자동 재접속이나 입력 재전송은 없다.
- 왕복 지연 표시는 WebSocket ping/pong 시간이다. 키 입력→CLI 처리→화면 표시 지연이나 렌더링 FPS 측정값이 아니다.
- 기존 데몬의 최대 5MiB 시작 출력 재생을 사용한다. 정확한 화면 snapshot이 아니며, 크기 변경·terminal query·alternate screen 재생 오류 가능성이 남아 있다. 한도 초과는 화면에 안내한다.
- 모바일/PC 화면 크기가 달라질 때 실제 Claude/Codex의 redraw 검증이 필요하다.
- 외부 편집기 호출(EDITOR)은 웹에서 지원하지 않는다. CLI 자체 편집과 별도 입력창만 사용한다.
- 네이티브 앱 대비 성능 수치는 아직 없다. Android IME·가상 키보드·화면 회전·백그라운드 복귀·Tailscale 망에서 직접 검증해야 한다.

참고: https://xtermjs.org/docs/guides/flowcontrol/ · https://tailscale.com/docs/features/tailscale-serve

### 2026-09-19 실행 결과

- 격리된 실제 Emacs가 연결 중인 세션을 웹에서 인수하고 CLI PID 유지·후속 입력 성공을 확인했다.
- Rust runtime 빌드 성공; 통합 테스트 1개(복수 시나리오) 통과, 약 2.2초.
- agent-browser에서 로그인 → Demo 생성 → 별도 입력창 한글 붙여넣기 → Enter → `REPLY: 안녕하세요` 표시 확인.
- 390×844 viewport에서 터미널·입력창·특수키 표시 확인, 브라우저 오류 없음. 실제 Android 기기 검증은 아니다.
- 테스트 가짜 CLI와 데모 웹 서버는 검증 후 종료했다. 개인 Claude/Codex 세션은 시작하거나 종료하지 않았다.

### Ghostel 연결 인식 수정

실제 Ghostel의 `process-command`는 직접 argv가 아니라 `/bin/sh -c "… exec … attach … TOKEN"` 형태다. 세션 경로·PID에 더해 직접 argv의 토큰 또는 해당 shell wrapper의 마지막 토큰을 검증한다. 통합 테스트도 shell wrapper로 변경했으며, 실제 실행 중인 Ghostel 연결에는 해제 없는 매칭 검사를 수행했다. 전환 버튼은 처리 중 상태와 실패 이유를 직접 표시한다.

### PC 반환 검증

웹소켓 종료 완료를 기다리기 전에 PTY 연결과 attach 잠금을 해제한다. 명시적 반환 API는 연결 ID를 확인하고 데몬의 연결 해제 및 Emacs의 새 연결 완료를 확인한다. 수신/ACK가 멈춘 브라우저의 반환과 CLI PID 유지도 테스트한다.

실제 버튼 왕복 테스트(agent-browser 필요):

```sh
EAM_WEB_BROWSER_TEST=1 eam/var/remote-web-venv/bin/python eam/experiments/remote-web/test_gateway.py
```

### 자동 복귀와 버퍼 재사용

- 일반 WebSocket 종료 및 갑작스러운 TCP 단절 후 같은 CLI PID로 Emacs 복귀를 검증했다.
- `eam-attach`는 해당 세션의 연결이 끊긴 기존 터미널 버퍼를 우선 재사용한다. 버퍼 객체·미전송 초안을 유지하고 CLI를 재시작하지 않는다. 버퍼가 이미 닫혔으면 새로 생성한다.
- Ghostel 터미널 화면 모델은 재연결 때 다시 초기화한다. 기존 스크롤 위치와 완전한 화면 복원 보장은 별도다.
- 실행 중인 Emacs에는 변경된 `eam-terminal.el`, `eam-persistent.el`을 다시 로드해야 한다.

## 합의한 후속 방향

Python gateway는 프로토타입으로 유지하고, Android + Tailscale에서 실제 사용성을 검증한 뒤 Rust 바이너리로 통합한다.

- `eam-runtime serve`: HTTP/WebSocket, 인증, 폴더 탐색·생성, 세션 목록·생성, 조작권 전환 및 복귀.
- `eam-runtime daemon SESSION`: 기존처럼 세션마다 독립 프로세스로 PTY와 CLI를 유지.
- 동일 바이너리를 사용하되 웹과 모든 PTY를 단일 프로세스로 합치지 않는다. 웹 서버 업데이트·재시작으로 CLI 작업이 종료되지 않아야 한다.
- 브라우저 UI는 현재 xterm.js 기반 구현을 활용한다. Android 네이티브 앱은 아직 만들지 않는다.
- 조작권 전환 프로토콜 개선은 별도 과제다. Rust 이식만으로 Emacs 연결 식별·화면 복원 문제가 해결된다고 가정하지 않는다.

### 다음 실험: Android + Tailscale

실제 PC 한 대와 Android 한 대를 같은 tailnet에 연결한다. 아래 항목을 실제 기기에서 확인한 후 Rust 통합 범위를 결정한다.

1. PC에서 실행 중인 세션을 휴대폰에서 선택해 조작권 가져오기.
2. 한글 요청 작성·붙여넣기·전송, CLI 승인 선택.
3. 키보드 표시·숨김 및 화면 회전 후 터미널 사용성.
4. 탭 종료·화면 잠금·Wi-Fi/셀룰러 전환 후 단절 감지와 Emacs 자동 복귀.
5. 기존 Emacs 버퍼·미전송 초안·CLI PID 유지.
6. 휴대폰에서 폴더 탐색·생성 후 새 세션 실행.

기록할 결과: 화면 가독성, 입력 누락·중복 여부, 체감 지연, 자동 복귀 시간, 실패 시나리오. 화면 잠금과 네트워크 전환은 실제 기기 검증 전까지 통과로 간주하지 않는다.
