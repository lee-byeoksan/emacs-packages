# CLI 원본 기반 history

## 대화 식별과 조회

`C-c a h` / `eam-history`는 현재 세션의 기록을 선택창 없이 연다. 세션의 인증 루트와 telemetry ID/path를 사용하고, 필요한 경우 소유 CLI 프로세스가 열어 둔 대화 파일을 확인한다. 파일의 대화 ID까지 대조하며 같은 디렉터리의 최신 항목을 현재 대화로 추정하지 않는다. 기록 저장 전·경로 없음·ID 불일치는 이유를 안내한다. `C-c a o` / `eam-history-open`은 provider와 저장 대화를 직접 선택하는 별도 진입점이다.

구현은 `native/src/history.rs`, `usage.rs`, `lisp/eam-native-history.el`이다. 조회는 로컬 기록 읽기이며 모델 호출이 아니다. provider 대화 사본이나 검색 인덱스를 EAM에 만들지 않는다. 사용량 관찰을 위한 세션별 callback은 [native runtime](native-runtime.md)에 별도로 설명한다.

## 지원 형식과 상한

- Claude: `projects/*/*.jsonl`의 root user/assistant text. isMeta, isSidechain, tool_result, tool_use, thinking은 제외한다.
- Codex: `state_5.sqlite`의 threads 목록. 저장 대화 선택에서는 `thread_history_1.sqlite`의 userMessage/agentMessage 또는 rollout JSONL의 response_item/message를 읽는다. 현재 세션 직접 조회는 식별한 transcript 파일을 검증해 연다. 파일 경로를 얻지 못하면 다른 대화를 대신 열지 않는다.
- SQLite는 읽기 전용 연결이다. 끝의 미완성 JSONL 행은 다음 갱신까지 제외한다. CLI 내부 스키마는 안정 API로 가정하지 않는다.
- 표시 페이지는 최대 65,536자 또는 더 작은 사용자 상한. 이전 기록도 같은 상한을 지키며 출력 undo는 비활성화한다.
- worker 결과 최대 2Mi 문자, 개별 native 행 최대 8MiB, 목록 최대 300개, Claude 탐색 최대 10,000개 파일. 대화 식별 검사는 앞부분 1MiB 범위이므로 범위 안에서 ID를 확인하지 못하면 거절할 수 있다.
- `p`/`n` 페이지, `g` 열린 대화 최신 기록, `q` 닫기. CLI 안에서 대화를 바꿨다면 CLI 버퍼에서 `C-c a h`를 다시 실행한다.

저장 순서의 사용자/assistant 텍스트 뷰다. 활성 분기 전체 재구성, 모든 버전의 중복 제거, 저장 전 스트리밍, 첨부 원본·도구 결과 표시는 보장하지 않는다. Markdown 원문과 간단한 강조를 제공하며 CLI 화면을 재현하지 않는다. 매 페이지에서 기록을 순회할 수 있고 30초 timeout·C-g 취소를 제공한다. `C-s`는 현재 페이지 검색이며 전체 대화 검색은 아직 없다.

## 원시 터미널 기록과 진단

`eam-record-terminal`은 기본 nil이다. 활성화한 뒤 만든 세션만 원시 출력을 보관하며 이미 실행 중인 데몬의 설정은 바꾸지 않는다. 일반 history는 이 기록에 의존하지 않는다. `C-u C-c a o`로 원시 진단 파일을 직접 선택할 수 있다.

원시 기록 검색은 Rust helper가 청크 경계를 고려해 바이트 단위로 수행한다. 검색어는 최대 UTF-8 4096바이트이며 페이지가 더 작으면 추가 제한한다. 첫 일치 위치 주변만 표시하고 조회 버퍼 종료/취소 시 해당 helper를 정리한다. ANSI 코드 사이에 나뉜 글자는 화면에 보이는 단어와 바이트 검색 결과가 다를 수 있다. 기록은 완전한 터미널 snapshot이나 provider 대화 저장소 백업이 아니다.

현행 데몬은 선택적 raw 기록 쓰기에 실패하면 raw 기록을 중단하고 CLI를 유지한다. 완료되지 않은 청크·전원 장애 무손실·전체 입력 기록은 보장하지 않는다. 과거 프로토타입의 기록 실패 시 CLI 중단 정책과 구분한다. 자동 보관 기간/삭제 정책은 없으며 사용자 기록은 문서 정리·패키지 삭제의 대상이 아니다.

`native/tests/runtime.rs`, `tests/native-history-test.el`, `tests/history-test.el`에서 현재 ID 귀속·다른 대화 거절·한글/코드·페이지 상한·undo·취소·원본 불변을 검사한다. 과거 측정과 GUI 미검증 범위는 [검증 요약](validation.md)을 따른다.
