# EAM 문서

현재 사용법, 구현 구조와 설계 판단을 보관한다. 일회성 실행 로그·스크린샷·측정 JSON·시험용 소스는 문서로 보관하지 않는다. 이전 원본은 Git 이력에서 확인할 수 있으며, 새 실행 산출물은 Git에서 제외된 `eam/var/`에 둔다.

## 사용·설치

- [사용 안내](user-guide.md): M-x, 키, 세션·history·worktree 사용 흐름.
- [원격 접속](remote-access.md): Rust 웹 서버 실행, Tailscale, PC·모바일 제어권 전환.
- [패키지 설치](package-testing.md): use-package, 로컬 로드, 배포 및 설치 테스트.
- [업데이트·복구](setup-and-updates.md): 지원 환경과 문제 확인 순서.

## 구현·설계

- [제품 요구사항](PRD.md) / [로컬 HTML](PRD.html): 제품 목적과 요구사항. 작성 이후 추가 기능은 현재 사용 안내와 구현 문서를 따른다.
- [설계 결정](design-decisions.md): 선택 이유, 폐기한 대안, 보류 사항.
- [코드 구조와 native runtime](native-runtime.md): 모듈 책임, 빌드, 프로세스, 저장 파일.
- [PTY 백엔드](pty-backend.md): 연결·종료·크기·출력 상한.
- [원본 대화 history](native-history.md): 현재 세션 식별, 저장 형식, 페이지 제한.
- [세션 관찰·메모·quick](session-observation-validation.md): 상태 추정, 메모 수명, 임시→지속 전환.
- [알림](notifications.md), [리뷰 의견](review-comments.md), [worktree](worktrees.md), [caffeine](caffeine.md).

## 검증·조사

- [검증 요약과 한계](validation.md): 재현 명령, 최신 배치 결과, 과거 측정의 적용 범위.
- [Claude 스크롤 조사](claude-scroll-validation.md): 실제 GUI 계측과 아직 확정하지 못한 원인.
- [abtop 분석](abtop-feature-review.md), [Orca·Paseo 비교](orca-paseo-feature-gaps.md): 조사 시점의 참고 자료. 기능 목록은 구현 약속이 아니다.

## 유지 규칙

기능을 변경하면 해당 구현 문서와 사용 안내를 갱신한다. 날짜별 진행 일지를 새 문서로 계속 추가하지 않는다. 새 설계 판단은 결정 문서에 이유와 한계를 남기고, 측정은 환경·수치·검증 범위가 필요한 요약만 유지한다. 배치 성공, 실제 CLI 연결, GUI 관찰, 사용자의 물리 입력 확인을 구분한다. PRD HTML은 사용자가 요청한 열람 산출물이므로 유지한다.
