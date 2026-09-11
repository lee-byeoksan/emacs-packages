# 전용 PTY 데몬 실험

2026-09-16 Python 초기 실험 자료다. 현재 제품은 Rust runtime으로 전환했으며 [제품 안내](../../docs/pty-backend.md)를 따른다. 아래 실험의 용량·측정 결과는 당시 기준이다.

## 구현

```
Ghostel ↔ attach client ↔ Unix socket ↔ PTY owner daemon ↔ fake CLI
```

- 데몬이 CLI의 PTY를 소유하고 Emacs와 독립된 프로세스로 실행된다.
- select 기반 비차단 I/O. 화면 모델·copy-mode·마우스 모드·alternate screen을 추가하지 않는다.
- 출력은 ANSI/UTF-8를 해석하거나 재인코딩하지 않고 전달한다. 한글 입력, bracketed paste, 터미널 크기 변경을 전달한다.
- 일반 출력은 Ghostel 스크롤백에 직접 쌓인다. CLI 자체가 mouse mode/alternate screen을 사용하는 경우 그 동작은 그대로 남는다.
- 0700 런타임 디렉터리의 Unix socket에 단일 클라이언트만 연결. 두 번째 연결은 기존 연결을 탈취하지 않는다.
- 출력 전송 큐 256KiB, 재연결용 시작 기록 1MiB. 각 방향의 입력/출력도 제한한다. 원시 터미널 출력은 디스크에 저장하지 않는다.
- UI가 멈춰 출력 큐가 넘으면 해당 연결을 끊고 CLI 출력은 계속 배출한다. **이 연결에는 출력이 누락될 수 있다.** 무손실 전달 보장이 아니며 이 정책은 제품 적용 전 재검토 대상이다.
- CLI 종료 시 소켓을 제거하고 종료 코드를 기록한다. 데몬 종료 시 자신이 생성한 프로세스 그룹을 정리한다. 별도 세션으로 탈출한 하위 작업까지 추적하는 supervisor는 아직 아니다.
- macOS에서 종료 중인 그룹에 대한 killpg가 EPERM을 반환하는 경우, 아직 waitpid로 회수하지 않은 소유 PID에 직접 신호를 보낸다.

## 직접 보는 방법 — 가짜 CLI만 실행

개발 소스가 load-path에 등록된 Emacs에서 다음 파일을 `M-x load-file`로 로드한다.

```
~/workspace/emacs-ai/eam/experiments/pty-daemon/ghostel-probe.el
```

1. `M-x eam-pty-probe-new`: 한글·색상·코드를 포함한 500줄 출력과 입력 대기 화면을 연다.
2. 트랙패드로 위아래로 스크롤한다. 첫 줄 `ROW 0000`과 마지막 줄 `ROW 0499`를 확인할 수 있다.
3. 일반 입력은 그대로 echo된다. 테스트 CLI에서 **q 하나를 입력하면 CLI와 데몬이 종료**된다.
4. detach는 `C-x k`로 실험 버퍼를 닫는다. 프로세스 종료 확인이 나오면 승인한다. 종료되는 것은 연결 클라이언트이며 백그라운드 CLI는 유지된다.
5. `M-x eam-pty-probe-attach`로 같은 데몬에 다시 연결한다.
6. Emacs까지 종료할 경우 먼저 `M-: eam-pty-probe-runtime` 값을 기록한다. 새 Emacs에서 실험 파일을 다시 로드한 후 `eam-pty-probe-attach`에 그 `/tmp/eam-pty-…/` 디렉터리를 지정한다.
7. 마지막에는 실험 CLI에서 q로 종료한다. 런타임의 작은 메타데이터·진단 파일은 남는다. 개인 AI 세션 목록에는 등록되지 않는다.

이 명령은 experiments 안에만 존재하며 배포 패키지에서 제외된다. 실제 Claude/Codex를 시작하는 UI는 추가하지 않았다.

## 검증과 측정

실행 환경: 이 개발 Mac, Python 3, Emacs 30.1, 로컬 Ghostel 모듈. 모든 테스트는 가짜 CLI를 사용했다. GUI 프레임을 띄우지 않았다.

```sh
python3 eam/experiments/pty-daemon/test_relay.py
python3 eam/experiments/pty-daemon/benchmark.py
/Applications/Emacs.app/Contents/MacOS/Emacs --batch -Q -L eam/lisp \
  -l eam/experiments/pty-daemon/ghostel-test.el -f ert-run-tests-batch-and-exit
python3 eam/experiments/pty-daemon/emacs-restart-test.py
```

- PTY 테스트: 분할된 UTF-8 입력·bracketed paste 원문 보존, resize, 단일 연결, detach 후 동일 PID, 정상 exit/socket 정리, malformed packet 차단.
- 실제 Ghostel 모듈 테스트: 500줄 표시, transport가 mouse/alternate screen을 강제하지 않음, 출력 undo 비활성화, 버퍼 종료 후 재연결·한글 표시·CLI 종료.
- 별도 Emacs 프로세스 두 개: 첫 Emacs 종료 후 동일 CLI PID가 생존하고 두 번째 Emacs에서 출력 복원·종료 확인.
- 32MiB detached 출력과 멈춘 클라이언트: CLI가 막히지 않고 완료, 재생 한도 초과 표시 및 출력 큐 상한 확인.

최종 정상 종료까지 확인한 단일 측정 결과(612byte echo, 각 200회):

| 경로 | 중앙값 | p95 | 최대 |
|---|---:|---:|---:|
| 직접 PTY | 0.032ms | 0.046ms | 0.102ms |
| attach client + Unix socket + PTY 데몬 | 0.107ms | 0.893ms | 5.474ms |

이는 **입력/출력 왕복 측정이며 Ghostel 렌더링·GUI 스크롤 지연이 아니다.** 다른 터미널 대비 속도 배율이나 60fps를 의미하지 않는다. 같은 Mac의 스케줄링 영향을 받는 짧은 표본이므로 최악 지연 보장도 아니다.

5개 데몬 동시 유지, 각 32MiB(총 160MiB) 출력 전후 owner 프로세스 RSS:

| 세션 | 출력 전 KiB | 출력 후 KiB | 증가 KiB |
|---|---:|---:|---:|
| 1 | 19264 | 19712 | 448 |
| 2 | 19664 | 20224 | 560 |
| 3 | 19296 | 19680 | 384 |
| 4 | 19488 | 20144 | 656 |
| 5 | 19184 | 19760 | 576 |

출력량만큼 메모리가 증가하지 않았지만 Python 프로세스 기본 비용이 데몬당 약 19MiB 있다. 이 RSS에는 Emacs·Ghostel·CLI·attach client가 포함되지 않는다. C/Rust 구현 또는 여러 세션을 한 데몬에 수용할 필요성은 이 비용과 장애 격리를 함께 보고 판단한다.

## 가장 큰 미해결점: 화면 복원

프로세스 생존과 화면 복원은 별개다. 현재는 시작 시점부터 1MiB 이하인 출력만 메모리에서 재생한다. 상한을 넘으면 기록을 버리고 재연결 시 복원 불가를 표시한다. ANSI/UTF-8 중간 지점부터 잘린 기록을 재생하지 않는다.

**상한 이내의 재생도 일반 TUI에서 정확한 snapshot이 아니다.** 실행 중 크기 변경, 과거 terminal query에 대한 중복 응답, alternate screen 전환 등은 최종 화면을 잘못 복원할 수 있다. 따라서 가짜 선형 로그의 복원 통과를 Claude/Codex의 화면 복원 보장으로 해석하면 안 된다. 연결이 없는 동안 CLI가 터미널 질의 응답을 기다리면 PTY를 읽는 것만으로는 해결되지 않는다.

## 다음 검증 게이트

1. 실제 GUI에서 Ghostel 단독과 이 실험의 스크롤 체감·관성·한글 조합을 같은 출력으로 비교. 아직 미실시.
2. 출력 재생 대신 현재 화면/모드 snapshot 또는 CLI의 신뢰할 수 있는 redraw 경로를 검증. resize 이력, query, alternate screen, 승인 대기·입력 중인 프롬프트를 포함.
3. 실제 Claude/Codex의 연결 없는 실행·재연결·종료 및 native history/editor/알림 연동을 별도 검증. 아직 미실시.
4. 과부하 시 연결 해제·누락 대신 backpressure 또는 제한된 복구 방식 중 제품 정책 결정. 입력 전달 확인과 crash 복구·socket 소유권·프로세스 트리 정리 강화.
5. 현재 제품 통합 결과는 Rust native runtime 문서를 참고한다.

현 단계에서는 전용 데몬으로 생존·전달 경로를 분리할 수 있음을 확인했다. 제품의 지속 세션 요구사항 전체를 대체한 상태는 아니다.
