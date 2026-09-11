# 설치·업데이트·복구

EAM은 Rust native runtime을 처음 사용할 때 빌드한다. 실행에 Python은 필요 없다. macOS 검증 환경은 Emacs for Mac OS X 30.1, Ghostel 0.53.0이다. Git은 worktree에 필요하다.

1. Ghostel과 공식 Claude/Codex CLI를 준비하고 CLI에서 구독 계정으로 로그인한다.
2. 최초 빌드를 위해 Rust/Cargo와 Xcode Command Line Tools를 준비한다. EAM은 이 도구를 자동 설치하지 않는다.
3. [use-package 예제](package-testing.md)를 적용하고 `M-x eam-native-build`로 빌드하거나 `eam-new` 첫 실행의 자동 빌드를 사용한다. 설정 로딩만으로 빌드·AI·새 GUI는 실행하지 않는다.
4. `C-c a n`으로 provider와 프로젝트를 선택한다. 기본 입력은 터미널이며 외부 편집 버퍼는 `C-c C-c` 반환, `C-c C-k` 취소다.

빌드 실패 로그는 `*EAM native build*`다. Cargo가 PATH에 없으면 `~/.cargo/bin/cargo`를 탐색한다. 다른 경로는 `eam-native-cargo-executable`로 지정한다. 누락된 C 컴파일러/SDK·의존성 다운로드 실패를 해결한 뒤 `eam-native-build`를 다시 실행한다. 미리 빌드한 바이너리는 `eam-native-executable`에 절대 경로로 지정할 수 있다.

일반 지속 세션에서 Emacs만 종료했다면 `eam-attach`로 살아 있는 세션을 연결한다. quick은 연결 종료 시 CLI도 종료되므로 유지하려면 닫기 전에 `eam-persist`를 실행한다. CLI 자체가 종료됐으면 `eam-resume`에서 저장 대화를 선택해 새 CLI로 재개한다. Python 구현에서 실행한 기존 세션은 자동 변환하지 않으며 native 사용을 위해 정상 종료 후 재개한다.

업데이트는 패키지 코드를 갱신한 뒤 새 Emacs에서 로드한다. native 소스가 달라졌으면 새 캐시에 빌드하며 실행 중인 바이너리는 덮어쓰지 않는다. `eam-directory`를 `~/.eam/`으로 지정하면 Emacs 프로필을 교체해도 세션 기록·빌드 캐시를 따로 유지할 수 있다. provider 원본 대화 위치는 바꾸지 않는다.

[세부 runtime·한계](native-runtime.md), [패키지 검증](package-testing.md), [사용 매뉴얼](user-guide.md). 이전 doctor/setup/GUI Python 스크립트는 당시 개발 실험이며 현재 설치 절차의 필수 단계가 아니다.
