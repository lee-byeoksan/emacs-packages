# EAM — Emacs AI Management

macOS Emacs에서 공식 Claude Code·Codex CLI를 기존 로그인으로 실행하고 관리한다. 터미널 직접 입력을 기본으로 하며, 긴 입력은 일반 Emacs 편집 버퍼로 왕복한다. 버전은 **0.1.0**이다.

- [사용 매뉴얼](docs/user-guide.md): 명령·키·세션·history·worktree.
- [설치와 use-package](docs/package-testing.md): 로컬 소스 또는 Git 설치.
- [원격 접속](docs/remote-access.md): Tailscale·모바일 웹 터미널·제어권 전환.
- [문서 목차](docs/README.md): 구조·구현·설계 결정·검증·기능 조사.
- [PRD](docs/PRD.md) / [로컬 HTML](docs/PRD.html).

## 실행과 세션

`eam-new`는 provider를 자동완성으로 선택해 새 지속 세션을 만든다. `eam-detach`는 화면을 닫고 CLI를 유지하며 `eam-attach`로 다시 연결한다. `eam-quit` 또는 CLI 자체 종료는 세션을 끝낸다. 저장 대화는 `eam-resume`으로 재개한다.

`eam-quick`은 짧은 질문용으로, 연결이 끊기면 CLI도 종료한다. 계속 사용할 세션은 닫기 전에 `eam-persist`로 지속 세션으로 전환한다. 모든 세션은 독립 Rust PTY 데몬에서 실행하며 tmux는 사용하지 않는다.

`C-c a l`은 세션 목록, `C-c a h`는 현재 세션 대화 직접 조회, `C-c a o`는 다른 저장 대화 선택이다. 기록 원본은 provider가 보관한다. 원시 터미널 기록은 기본 꺼짐이고 진단할 때만 켠다.

## 의존성과 데이터

Ghostel은 별도 의존성이다. 실행 중 Python은 필요 없다. 첫 native 기능 사용 시 Rust/Cargo와 C 빌드 도구로 컴파일하며, 빌드 로그를 일반 버퍼에 표시한다. `M-x eam-native-build`로 미리 빌드할 수 있다. 설치·require만으로 GUI·CLI·AI 호출을 시작하지 않는다.

기록과 바이너리는 `eam-directory`로 지정하며 권장 예제는 `~/.eam/`이다. 개인 Emacs/CLI 설정 파일을 자동 수정하지 않는다. 시스템 프롬프트·스킬·컨텍스트 자동 삽입이나 자동 제목 생성 모델 호출을 하지 않는다.

## 개발·검증

저장소 루트에서:

```sh
mkdir -p eam/var/validation
bash eam/scripts/test-native.sh > eam/var/validation/native.log 2>&1
bash eam/scripts/test-native-package.sh > eam/var/validation/package.log 2>&1
```

현재 코드·가짜 CLI·설치 경로 검증은 [검증 요약](docs/validation.md)을 따른다. 실제 GUI 한글 조합·스크롤 체감은 batch 성공과 구분한다. PTY 재생은 완전한 화면 snapshot이 아니다.

제품 코드는 `lisp/`, `native/`, 테스트는 `tests/`, `native/tests/`에 있다. `bridge/`·`experiments/`는 과거 실험이며 기본 실행 경로가 아니다. 로그·스크린샷·측정 산출물은 Git 제외된 `var/`에 두고 `docs/`에는 유지할 설명과 결정만 남긴다.
