# emacs-packages

개인적으로 개발하는 Emacs 패키지를 모아 관리하는 저장소다. 각 패키지는 자신의 디렉터리 안에 소스·문서·테스트·빌드 도구를 둔다.

| 패키지 | 목적 | 문서 |
| --- | --- | --- |
| [EAM](eam/) | 공식 AI CLI의 Emacs 실행·지속 세션·입력 편집·대화 조회 | [사용 안내](eam/docs/user-guide.md), [PRD](eam/docs/PRD.md) |

```text
emacs-packages/
├── README.md
├── .gitignore
├── .elpaignore          # VC 설치 시 저장소 전체 컴파일의 제외 경로
└── eam/
    ├── lisp/
    ├── bridge/
    ├── docs/
    ├── examples/
    ├── scripts/
    ├── tests/
    ├── experiments/
    └── var/             # 로컬 의존성·시험 산출물·기록 (Git 제외)
```

새 패키지는 EAM과 같은 수준의 디렉터리에 추가한다. 패키지별 버전과 빌드는 독립적으로 관리한다. 저장소 루트의 `.elpaignore`에는 각 패키지의 테스트·실험·생성 파일 제외 경로도 반영한다.

## EAM 실행·검증

저장소 루트에서:

```sh
bash eam/scripts/start.sh       # 새 격리 Emacs GUI 실행
bash eam/scripts/test.sh        # 배치 검사, GUI·실제 AI 호출 없음
python3 eam/scripts/build-package.py --output-dir eam/var/packages/my-build
```

EAM 디렉터리에서 실행할 때는 `cd eam` 후 기존 `scripts/...` 명령을 그대로 사용한다. 현재 로컬 checkout 이름은 `~/workspace/emacs-ai`이며 저장소 이름 변경만으로 로컬 폴더를 바꾸지는 않았다. 따라서 이 checkout의 EAM 소스는 `~/workspace/emacs-ai/eam/lisp`다.

기존 Emacs에 로드하는 예시는 [eam-init.el](eam/examples/eam-init.el)에 있다. [use-package VC 설치 예제](eam/examples/eam-vc-init.el)도 제공한다. 새 저장소를 `~/workspace/emacs-packages`에 clone하면 예시의 load-path를 `~/workspace/emacs-packages/eam/lisp`로 바꾼다. EAM 데이터는 코드 위치와 독립적으로 `~/.eam/`에 둘 수 있다.

VC 설치의 저장소 URL은 `git@github.com:lee-byeoksan/emacs-packages.git`, Lisp 경로는 `eam/lisp`다. 구조 변경이 원격에 커밋·푸시되기 전에는 새 경로로 원격 설치할 수 없다.

## 디렉터리 이동 시 참고

기존 EAM 파일과 로컬 `var/` 자료는 `eam/` 아래로 이동했다. 기존 시험 기록·프로필·worktree 메타데이터 안의 절대 경로는 과거 상태 그대로 보존한다. 이전 프로필은 그대로 실행하지 말고 새 위치에서 다시 준비한다. 기존 실사용 `~/.eam/` 및 개인 Emacs 설정은 변경하지 않는다.

현재 위치의 새 시험 프로필 준비 방법은 [패키지 안내](eam/docs/package-testing.md)를 참고한다. 실행 중인 Emacs에 로드된 코드는 자동 갱신되지 않으므로 사용 중인 작업을 정리한 뒤 새 위치의 코드로 로드한다.
