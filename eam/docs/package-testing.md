# 패키지 설치와 검증

현재 EAM 0.1.0은 native 실행 파일을 최초 사용 시 로컬에서 빌드한다. Ghostel, Rust/Cargo와 C 빌드 도구를 먼저 준비한다. 최초 빌드 이후 캐시된 실행 파일에는 Python이나 Cargo가 필요 없다. [빌드·캐시·지원 범위](native-runtime.md)를 참고한다.

## 로컬 소스로 먼저 시험

[로컬 use-package 예제](../examples/eam-init.el)를 기존 Emacs에서 평가하면 된다. 로딩만으로 GUI·CLI·컴파일을 시작하지 않는다. `M-x eam-native-build`로 미리 빌드할 수 있고 `C-c a n` / `eam-new`의 첫 사용 시 자동 빌드도 가능하다. 빌드는 백그라운드에서 진행하며 로그 버퍼가 즉시 열린다. 완료 후 EAM 명령을 다시 실행한다. 로그 버퍼의 `C-c C-k`는 빌드 취소다. 기록과 바이너리는 예제의 `~/.eam/`에 보관한다.

## use-package — 여러 패키지 저장소

```elisp
(use-package eam
  :vc (:url "git@github.com:lee-byeoksan/emacs-packages.git"
       :branch "main"
       :lisp-dir "eam/lisp"
       :rev :newest)
  :demand t
  :init
  (setq eam-directory (expand-file-name "~/.eam/")
        eam-native-auto-build t
        eam-terminal-redraw-delay 0.016)
  :config
  (require 'eam-app)
  (eam-keys-mode 1))
```

`:lisp-dir`은 저장소 루트 기준이다. `.elpaignore`로 테스트·예제·과거 `.el` 데이터를 바이트 컴파일에서 제외한다. VC 체크아웃에는 과거 실험 파일이 물리적으로 남지만 실행 경로에는 쓰이지 않는다. 실행 파일 소스·문서는 EAM 리소스 루트에서 찾는다. 변경이 아직 푸시되지 않았다면 먼저 로컬 예제를 사용한다.

기존 설치의 URL·디렉터리는 설정 변경만으로 자동 이전되지 않는다. 필요하면 CLI를 정상 종료하고 package-delete 후 새 설정으로 재설치한다. `~/.eam/`과 provider 대화 기록은 지우지 않는다. 새 Emacs에서 로드하면 이전 키맵/함수 정의가 남는 문제를 피할 수 있다.

원격 서버도 Emacs와 함께 시작하려면 위 `:config`에 `(require 'eam-remote)`와 `(eam-remote-mode 1)`을 추가한다. [원격 접속 설정](remote-access.md)을 따른다.

## tar 생성과 자동 검증

저장소의 `eam/`에서 실행한다. 모두 GUI와 실제 AI를 실행하지 않는다. 빌드 의존성은 처음 다운로드될 수 있다.

```sh
bash scripts/build-package.sh var/packages/native-local
bash scripts/test-native.sh
bash scripts/test-native-package.sh
```

tar 생성은 출력 경로를 덮어쓰지 않는다. tar에는 제품 Lisp·Rust 소스·Cargo.lock·문서만 넣고 Python·가짜 CLI·테스트 코드는 제외한다. 생성된 tar는 `M-x package-install-file`로 설치할 수 있다.

설치 테스트는 기존 개발용 `var/deps/ghostel`을 사용하며 임시 프로필 안에서 실제 설치·첫 release 컴파일·Cargo 없는 캐시 재사용·PTY/history/알림·제거 후 데이터 보존을 확인한다. 개인 Emacs 설정은 수정하지 않는다. 특정 tar를 검사하려면 `bash scripts/test-native-package.sh /절대/경로/eam-0.1.0.tar`를 사용한다.

`test-app.sh`, `test-terminal.sh`, `test.sh`도 현재 native suite로 연결된다. 옛 Python 설치/프로필 스크립트와 당시 산출물은 역사적 도구이며 최신 검증의 기준은 위 두 native 스크립트다.

## 업데이트와 제거

소스 변경 시 소스 해시별 새 바이너리를 빌드한다. 이미 실행 중인 CLI가 사용하는 바이너리를 덮어쓰지 않는다. 옛 Python 세션을 native로 바꾸려면 정상 종료 뒤 새 세션 또는 `eam-resume`을 사용한다.

제거 전 CLI를 종료하고 `M-x package-delete RET eam RET`를 실행한다. 패키지 코드만 삭제하며 별도로 지정한 `~/.eam/` 기록·바이너리 캐시, provider 원본 대화, Ghostel은 남긴다. 전체 프로필 폴더를 수동 삭제하는 것은 패키지 제거와 다르다.

배치 테스트는 실제 GUI 한글 조합·트랙패드·긴 실제 응답의 체감 지연을 대신하지 않는다. 평소 작업에서 문제를 발견하면 `명령/키 → 기대 동작 → 실제 동작`으로 알려주면 된다. 반복 확인을 위해 AI를 호출할 필요는 없다.
