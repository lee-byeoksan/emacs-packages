# 알림과 읽음 상태

현재 구현: `lisp/eam-notifications.el`, `eam-persistent-events.el`, `native/src/events.rs` 및 세션 manager. 사용 키는 [사용 안내](user-guide.md)를 따른다.

`eam-cli-notifications`는 새 CLI에 알림 실행 옵션을 추가하며 기본 t다. Codex는 OSC 9 알림 옵션, Claude는 Stop/Notification/PermissionRequest command hook으로 native 알림 함수를 호출한다. 개인 CLI 설정 파일을 영구 수정하지 않는다. 설치만으로 전역 hook을 등록하지 않으며, 기존 CLI 프로세스의 인수는 변경되지 않는다. 프롬프트·승인 결정·도구 인자를 모델 컨텍스트에 추가하지 않는다.

PTY 데몬이 OSC 9/777을 파싱해 순번이 있는 이벤트를 기록한다. Emacs는 원본 세션에 귀속해 수신하며 어떤 알림도 자동 승인하거나 입력을 보내지 않는다. 알림은 CLI가 보낸 이벤트이지 전체 목표 달성의 증거가 아니다. 특히 Claude Stop은 턴 종료다.

메모리에는 기본 100건, 제목·본문 각각 2048자만 유지한다. 같은 세션의 같은 미읽음 알림이 2초 내 반복되면 합친다. 잘린 표시 문자열만으로 합치지 않도록 원문 해시도 비교한다. 데스크톱 전달 중 다른 버퍼가 선택돼 있어도 원래 발신 버퍼 문맥을 유지한다.

`notification-read.json`의 읽음 순서를 events와 비교해 detached 세션에서도 미읽음을 조회한다. 호출자가 본 순서까지만 확인 처리하므로 뒤늦게 들어온 새 알림을 읽음으로 만들지 않는다. 이름·종료 상태와 별도 파일을 사용한다. 조회한 이벤트 tail은 최대 64KiB이며 전체 본문을 목록에 올리지 않는다.

`eam-notification-desktop`은 기본 nil이다. 활성화 시 Ghostel의 OS 알림 기능을 사용하며 alert 백엔드·OS 권한 설정에 따라 배너 대신 Emacs 메시지만 나올 수 있다. 과거 가짜 알림 1건에 대해 사용자가 macOS 배너를 확인했지만 모든 머신의 권한이나 실제 AI 완료 알림을 보장하는 결과는 아니다.

검증 소스는 `tests/notifications-test.el`, `native/tests/runtime.rs`다. UTF-8/OSC 분할 수신, 중복 억제, 세션 귀속, 읽음 복원, 닫힌 버퍼, 본문·건수 제한을 검사한다. 실제 CLI 버전별 이벤트 발생·배너·GUI 확인은 [검증 요약](validation.md)과 구분한다.
