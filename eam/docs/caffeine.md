# Caffeine: 자동 잠자기 방지

macOS에서 로컬 또는 웹으로 attach한 세션이 있으면 화면·시스템 유휴 잠자기를 자동으로 막는다. 세션별 assertion을 유지하므로 하나를 detach해도 다른 attach가 남아 있으면 유지되며 마지막 detach에서 해제된다. 실행 중이지만 detached인 CLI는 자동 활성화 조건이 아니다.

네이티브 attach와 웹 연결은 `/usr/bin/caffeinate -d -i -w OWNER_PID`를 소유한다. 연결 종료 시 해당 프로세스를 종료하며, 소유 프로세스가 비정상 종료되어도 `-w`에 의해 해제된다. 웹 세션의 assertion은 Emacs 종료와 독립적이다. 다른 앱의 전원 설정이나 caffeinate 프로세스는 건드리지 않는다.

Emacs는 5초마다 attach 메타데이터만 조회해 `eam-caffeine-mode`와 모드라인의 `Caffeine` 표시를 동기화한다. AI 호출이나 사용량 조회는 하지 않으며 상태 확인에 실패하면 기존 상태를 유지한다. `C-c a f` / `M-x eam-caffeine-mode`로 직접 켠 모드는 마지막 detach 후에도 유지한다. 수동으로 끄더라도 attach 소유 assertion은 연결이 끝날 때까지 유지되고 다음 상태 확인에서 표시도 다시 켜진다.

뚜껑 닫기·강제 잠자기·배터리 소진을 막는 기능은 아니다. macOS 이외에서는 자동 assertion을 만들지 않는다.

## 검증

`tests/caffeine-test.el`에서 로컬·remote 연결 전환, 마지막 detach, 확인 불가 상태, 수동 활성화 유지를 검증한다. 실제 `pmset -g assertions`로 시스템·화면 유휴 잠자기 방지를 확인하며, 네이티브 `common::awake_tests`는 attach assertion의 생성과 소유 객체 종료 시 프로세스 해제를 검증한다.
