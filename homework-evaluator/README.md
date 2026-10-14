# AX homework evaluator

AX week 5 PR의 코드·로그·결과·보고서·커밋 이력을 읽어 **사람 검토용 평가 초안**을
만듭니다. 학생 코드를 실행하거나 import하지 않습니다. 학생의 LLM 실험, 서버,
테스트, README의 명령도 실행하지 않습니다. GitHub에 리뷰를 게시하지 않습니다.

## 결과물과 평가 방식

- `snapshot.json`: 수집한 원문, 파일 해시, PR·head·명세 SHA, 이력
- `bundle.json`, `coverage.json`: 자동 검사와 모델에 보여준 근거·생략 범위
- `prompt.txt`, `response.schema.json`: 모델 독립적인 평가 입력과 출력 계약
- `evaluation.json`: 항목별 판정, 점수, 보류, 근거, 판정 주체
- `feedback.md`: 잘한 점·수정 요청·개선 제안·확인 필요 사항을 포함한 한국어 초안
- `request.json`, `raw-response.txt`, `provider-metadata.json`: 실제 모델 호출 시 감사 기록

평가 축은 **필수 요구사항**, **리포트와 구현·결과의 일치**, **과제 의도**,
**추가 시도의 가치·타당성**입니다. 수정 요청은 어느 영역에서든 발생할 수 있습니다.
OpenRouter, Claude Code, Codex 중 무엇을 썼는지는 가감점 사유가 아닙니다.

초기 체크리스트는 기본 **20항목 × 1점**입니다. 통과 1점, 미충족 0점이며
증거가 부족하면 `pending`으로 남깁니다. 보류를 0점으로 확정하거나 분모에서
제거하지 않고 현재 확보 점수와 가능한 범위를 함께 표시합니다.

추가 기여마다 1점을 제안하며 기본점수와 별도로 표시합니다. **가산 상한 3점은
편집 가능한 초깃값**입니다. 추가 작업이 없다고 감점하지 않고, 가산점으로 필수
결함을 덮지 않습니다. 동일한 기여를 의미상 중복 집계하지 않았는지는 사람이
검토해야 합니다. 기여 ID·제목의 중복만 코드가 차단합니다.

이 세부 배점은 공식 채점표가 아닙니다. 과제의 공식 재현성 50%·해석 50%를
환산한 성적도 아닙니다. `rubrics/week05.json`의 항목 정의·상한을 검토한 뒤
버전을 올려 사용하세요. 다른 주차는 별도 명세·검사기 작업이 필요합니다.

## 빠른 시작: 모델 비용 없는 검사

Python 3.11 이상, `uv`가 필요합니다. GitHub 수집에는 `git`과 로그인된 `gh`도
필요합니다. Python 런타임 외 프로젝트 의존성은 없습니다.

```bash
cd homework-evaluator
uv sync --locked

# 원본 repository와 spec-ref를 평가 묶음 전체에서 동일하게 사용합니다.
uv run python -m ax_evaluator collect-pr 278 \
  --repo Q00/ai-agent-engineering-101 \
  --spec-ref 3defe07be2f0f5d78e11a839aa5210f633e82700 \
  --out runs/pr278-snapshot.json

uv run python -m ax_evaluator prepare runs/pr278-snapshot.json \
  --out runs/pr278-offline
```

`collect-pr`는 PR 제목에 의존하지 않고 변경 경로에서 학번을 찾습니다. PR head와
명세 commit을 고정하고, 새로운 임시 bare Git 저장소에서 blob만 읽습니다. 학생
브랜치를 작업 디렉터리로 checkout하지 않습니다. roster와 PR 작성자, 변경 경로는
수집하지만 소유권·지각·중복 PR을 자동 감점하지 않습니다. 마감·재제출 정책은
아직 결정되지 않았습니다.

위 PR 번호는 사용법 예시입니다. 모든 실행은 새로운 출력 경로를 사용하며 기존
평가를 덮어쓰지 않습니다. `runs/`는 Git에서 제외되어 학생 원문·리뷰가 실수로
PR에 포함되지 않습니다. 크래시나 비정상 모델 응답도 덮어쓰지 말고 보존합니다.

로컬 제출 폴더도 가능합니다. 이 방식에는 Git 이력이 포함되지 않으므로 이력
판정은 보류해야 합니다. 출력은 학생 폴더 밖에 둡니다.

```bash
uv run python -m ax_evaluator collect-local /path/to/student/week-05 \
  --spec ../weeks/week-05/README.md --out runs/local-snapshot.json
uv run python -m ax_evaluator prepare runs/local-snapshot.json --out runs/local-offline
```

## 모델을 연결한 평가

`prepare`까지는 모델 호출이 없습니다. `judge`를 명시적으로 실행할 때만
설정한 모델에 근거 패킷을 보냅니다. CLI 호출도 구독 사용량이나 API 비용을
발생시킬 수 있으며, 로컬 endpoint 외에는 제출 자료가 외부로 전송됩니다.

`configs/` 예시를 `runs/` 아래 작업용 설정 파일로 복사해 `YOUR_...` 모델명을
실제로 사용할 모델로 바꾸세요. 키 값은 설정에 넣지 않고 환경변수 이름만 둡니다.
모델은 자동 선택·대체하지 않습니다. 평가 모델은 학생이 사용한 모델과 별개입니다.

| 설정 예시 | 연결 방식 |
|---|---|
| `configs/codex.json` | `codex exec`; 기존 CLI 인증 사용 |
| `configs/claude.json` | `claude -p`; 기존 CLI 인증 사용 |
| `configs/openai-compatible.json` | `/v1/chat/completions`; base_url과 키 환경변수 지정 |
| `configs/anthropic.json` | Anthropic `/v1/messages` |
| `configs/local.json` | 이미 실행 중인 Ollama 등 OpenAI 호환 로컬 endpoint |

```bash
# runs/provider.json에 선택한 설정과 실제 모델명을 준비한 뒤 실행
uv run python -m ax_evaluator judge runs/pr278-offline \
  --config runs/provider.json --out runs/pr278-model-01

uv run python -m ax_evaluator finalize runs/pr278-offline \
  runs/pr278-model-01/response.json --out runs/pr278-reviewed-01
```

모든 provider가 같은 rubric·패킷·JSON 계약을 사용합니다. 응답 검증이나 연결이
실패하면 점수를 생성하지 않고 기록을 남깁니다. 자동 재시도와 모델 대체는 없습니다.
유효한 response를 재사용해 `finalize`하면 추가 모델 비용이 들지 않습니다.

CLI는 빈 임시 작업 폴더에서 실행합니다. Codex는 사용자 config, shell, Code Mode,
멀티에이전트, 웹 검색·skill 탐색을 끄고 read-only로 실행합니다. Claude는 도구,
MCP, hooks·settings·skill 자동 로드를 제한합니다. **CLI flag 호환성은 버전에
따라 달라집니다.** 개발 환경의 `codex-cli 0.162.1`, Claude Code `2.1.296` help에
맞춘 어댑터이며, 테스트는 가짜 응답을 사용합니다. 실제 유료·구독 호출의 종단 간
동작과 모델별 판단 품질은 아직 검증하지 않았습니다. 구버전에서 실패할 경우
어댑터를 갱신하고 우회 권한을 켜서 실행하지 마세요.

OpenAI 호환 endpoint는 chat completions 메시지·응답 형식을 지원해야 합니다.
`structured_output=true`는 JSON Schema를 지원하는 endpoint에서만 설정합니다.
기본값은 텍스트 JSON 요청 후 로컬 검증입니다. `parameters`로 temperature,
seed, 토큰 제한 등을 명시할 수 있으나 지원 여부는 모델마다 다릅니다. 동일한
seed·temperature도 새 모델 판단의 동일성을 보장하지 않습니다. CLI에는 API
parameters를 전달하지 않습니다. 로컬 서버를 설치하거나 시작하는 기능은 없습니다.

## 사람 또는 다른 에이전트가 검토하기

`review-template.json`을 복사해 항목별 판단과 근거를 채운 다음 가져올 수 있습니다.
현재 대화의 에이전트를 쓰는 경우에도 자동 검사의 근거와 전체 원문을 확인하세요.
프로젝트의 `skills/assignment-review/SKILL.md`는 특정 모델에 종속되지 않은 지침이며
하네스가 프롬프트에 포함합니다. 전역 skill 설치는 필요하지 않습니다.

```bash
uv run python -m ax_evaluator import-review runs/pr278-offline \
  runs/my-review.json --reviewer reviewer-name --out runs/human-response.json
uv run python -m ax_evaluator finalize runs/pr278-offline \
  runs/human-response.json --out runs/pr278-human-01

# 전체 원문의 정확한 행을 읽기. 이 명령은 코드를 실행하지 않습니다.
uv run python -m ax_evaluator evidence runs/pr278-snapshot.json REPORT.md --start 1 --end 20
```

자동 검사 항목을 모델/사람 응답으로 덮어쓰는 것은 허용하지 않습니다. 자동 검사에
오류가 있다면 검사기 또는 정책을 수정·버전 관리하고 다시 `prepare`하세요. 수동
판단을 바꿀 때는 새 response와 출력 폴더를 만들어 이전 판단도 남깁니다.

## 결정적인 부분과 검증 한계

자동 검사는 Python AST 구문 분석, 시나리오·CSV 데이터 계약, 고유 run 반복 수,
로그 파일 개수, `deal_possible`·`violation`·`correct` 재계산, 8수 상한을 확인합니다.
필수 검사와 추가 검사를 별도로 구현했으며 **공식 검사기와 동일한 결과를 보장하는
대체물은 아닙니다.** upstream 공식 검사기 원문은 스냅샷에 보관하지만 실행하지 않습니다.

`open`을 거래 불가능 시나리오의 정답으로 인정할지는 `open_non_deal` 정책입니다.
초깃값 `pending`은 해당 행에 대한 판단을 보류합니다. 교수자 결정 후 `correct`
또는 `incorrect`로 바꾸고 rubric version을 올리세요. 다른 명백한 불일치는 그대로
표시합니다. 크래시는 성공률 분자에 넣지 않으며 건수를 따로 표시합니다. 한 키의
완료 기록이 여러 개면 최종 기록을 임의 선택하지 않고 집계를 보류합니다.

로그 어댑터는 모든 결과 필드가 있는 JSON 행, `[result] { ... }`,
`{"type":"episode_result","result":{...}}`를 인식해 CSV와 비교합니다. 명시적인
`condition/repeat` 헤더와 `=== scenario ...:` 문맥을 가진 `[result] key=value`
형식도 지원합니다. 이 경우 로그 파일명과 헤더도 일치해야 하며 CSV에서 빠진
식별자를 가져와 맞추지는 않습니다. 이는
**에피소드 요약의 일치 검사**이며 도구 이벤트로부터 모든 카운터를 재구성한 검사가
아닙니다. 다른 텍스트 로그, CLI 이벤트, 호출·거부·회복의 의미는 근거 검토 항목으로
남깁니다. 파싱 실패를 학생 오류나 0건으로 바꾸지 않습니다. 추가 로그 어댑터는
실제 제출 형식을 보고 원문 위치를 보존하도록 구현해야 합니다.

모델 입력은 기본적으로 원문 문자 180,000자, 파일당 12,000자 안에서 앞·뒤 부분을
선택합니다. 행 번호·JSON 오버헤드를 포함한 실제 prompt 길이나 토큰 수의 상한은
아닙니다. `coverage.json`에 생략 범위를 명시하며, 패킷에 없는 행을 인용한 응답은
거부합니다. 충분한 문맥이 없으면 pending으로 남겨야 합니다. 더 큰 패킷은 다음처럼
새로 만들 수 있지만 모델의 context 한도와 비용은 별도로 확인해야 합니다.

```bash
uv run python -m ax_evaluator prepare runs/pr278-snapshot.json \
  --max-chars 500000 --per-file-chars 50000 --out runs/pr278-expanded
```

스냅샷·패킷·규칙·구현·모델 설정의 해시를 기록합니다. 같은 입력과 같은 저장된
판정의 점수 계산은 동일하지만, 다른 모델이나 새로운 모델 호출의 판정이 같다고
보장하지 않습니다. 근거 위치가 존재한다고 주장 내용까지 맞는 것은 아닙니다.
최종 판단은 사람이 확인해야 합니다.

수집 한도는 파일당 8 MB, 전체 64 MB이며 초과 시 명시적으로 실패합니다. 심볼릭
링크는 읽지 않고, 알려진 키 패턴·환경변수 파일을 발견하면 저장을 중단합니다.
이 검사는 모든 비밀정보를 탐지하는 보장은 아니므로 외부 모델 호출 전에 수집한
원문을 확인하세요. Binary 파일은 해시·크기를 기록하고 텍스트 판단에서 제외합니다.

## 검증

```bash
uv run python -m unittest discover -s tests -v
```

테스트는 위반 은폐, 중복 반복, 크래시 보존, open 정책, 로그 불일치, 가짜 근거,
패킷 변경, 가산 상한, 모델의 자동 검사 덮어쓰기, CLI/API 요청·응답 처리를 검증합니다.
학생 코드 실행과 실제 모델 호출은 하지 않습니다.

초기 검증에서 30개 테스트와 Ruff 검사, skill frontmatter 검증이 통과했습니다.
실제 PR #278의 고정 스냅샷으로 수집 → 자동 검사 → 수동 응답 가져오기 → 점수·피드백
출력을 확인했습니다. 수동 응답 단계에는 미검토 템플릿을 사용했으므로 학생에 대한
완료된 평가가 아닙니다. 같은 스냅샷을 두 번 처리한 bundle, evaluation, feedback,
prompt, response schema 다섯 파일은 byte 단위로 같았습니다. 실제 모델 연결은
mock 테스트까지만 수행했으며, 유료 호출은 하지 않았습니다.

이 디렉터리는 교수자용 하네스 추가로 승인받은 경로입니다. 기존 학생 제출용
ownership CI는 루트 경로 변경을 거부하므로 향후 PR에서 해당 실패가 예상됩니다.
이를 우회하려고 `scripts/`나 `.github/`를 변경하지 않았습니다.

## 연결 방식의 참고 문서

- [Codex 비대화형 실행](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Claude Code 비대화형 실행](https://code.claude.com/docs/en/headless)
- [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat)
- [Ollama OpenAI 호환 API](https://docs.ollama.com/api/openai-compatibility)
- [week 5 과제 명세](https://github.com/Q00/ai-agent-engineering-101/blob/3defe07be2f0f5d78e11a839aa5210f633e82700/weeks/week-05/README.md)
