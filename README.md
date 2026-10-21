# 나만의 용돈 기입장 (budget_app)

파일(JSONL) 기반 **콘솔 가계부**입니다. 수입/지출 기록, 목록·검색, 월별 요약, 예산 초과 경고,
카테고리 관리, CSV 가져오기/내보내기를 제공합니다.

- **표준 라이브러리만 사용**합니다. `pip install`이 필요 없습니다.
- 저장 파일을 한 번에 읽지 않고 **제너레이터로 한 줄씩 스트리밍**합니다.
- 수정/삭제는 **임시 파일에 쓴 뒤 원자적으로 교체**하므로, 저장 도중 프로그램이 종료되어도 원본이 깨지지 않습니다.
- 오류가 나면 스택트레이스 대신 **`[오류] 원인` + `[힌트] 해결 방법`** 을 출력하고 0이 아닌 종료 코드로 끝납니다.

---

## 1. 실행 방법

### 요구 사항
- Python **3.10 이상**

### 실행
저장소 최상위 폴더(이 README가 있는 곳)에서 실행합니다.

```bash
python -m budget_app --help              # 전체 명령 목록
python -m budget_app <command> --help    # 명령별 사용법과 예시
```

| 명령 | 설명 | 입력 방식 |
| --- | --- | --- |
| `add` | 거래 추가 | 대화형 |
| `list` | 거래 목록(최신순) | 옵션 |
| `search` | 조건 검색(최신순) | 옵션 |
| `summary` | 월별 요약 + 예산 사용률 | 옵션 |
| `update` | 거래 수정 | **옵션 방식으로 고정** |
| `delete` | 거래 삭제 | 옵션 |
| `budget set/get/list` | 월 예산 설정/조회 | 옵션 |
| `category add/list/remove` | 카테고리 관리 | 옵션 (`add`는 `--name` 없으면 대화형) |
| `import` / `export` | CSV 가져오기/내보내기 | 옵션 |
| `backup [create/list/restore]` | 백업/복원 (보너스) | 옵션 |
| `recurring add/list/remove/apply` | 반복 거래 (보너스) | 옵션 |

### 공통 옵션
| 옵션 | 설명 |
| --- | --- |
| `--help` | 사용법 출력 (모든 명령에서 사용 가능) |
| `--data-dir DIR` | 데이터 폴더 지정 (기본 `./data`). 환경변수 `BUDGET_APP_DATA_DIR`로도 지정 가능 |
| `--verbose` | 실행 로그와 실행 시간(ms)을 화면(stderr)에도 출력 |

공통 옵션은 명령 앞/뒤 어디에 써도 됩니다. 예: `python -m budget_app list --data-dir ./my-data`

---

## 2. 저장 파일 위치와 형식

- 기본 위치: 실행한 폴더 기준 **`./data/`** (`--data-dir`로 변경)
- 형식: **JSONL** (UTF-8, 한 줄에 JSON 객체 하나)

| 파일 | 내용 | 한 줄 예시 |
| --- | --- | --- |
| `transactions.jsonl` | 거래 내역 | `{"id": "TX-000001", "type": "expense", "date": "2024-01-15", "amount": 15000, "category": "food", "memo": "점심", "tags": ["meal"]}` |
| `categories.jsonl` | 카테고리 목록 | `{"name": "food"}` |
| `budgets.jsonl` | 월 예산 | `{"month": "2024-01", "amount": 500000}` |
| `recurring.jsonl` | 반복 거래 규칙 (보너스) | `{"id": "RC-000001", "day": 25, "type": "income", "category": "salary", "amount": 3000000, "memo": "월급", "tags": []}` |
| `app.log` | 실행 로그(데코레이터가 기록) | |
| `backups/backup-YYYYMMDD-HHMMSS.zip` | 백업 파일 (보너스) | |

### 거래(Transaction) 필드
| 필드 | 필수 | 규칙 |
| --- | --- | --- |
| `id` | 자동 | `TX-000001` 형식, 유일. 명령에서는 `TX-000001`, `tx-1`, `1` 모두 허용 |
| `type` | Y | `income` 또는 `expense` (대화형 입력 시 `수입`/`지출`도 허용) |
| `date` | Y | `YYYY-MM-DD`, 실제 존재하는 날짜 |
| `amount` | Y | 양수 정수(원). `15,000`처럼 쉼표 입력 가능 |
| `category` | Y | `categories.jsonl`에 등록된 이름 |
| `memo` | N | 200자 이하 문자열 |
| `tags` | N | 문자열 목록. 입력은 쉼표 구분(`meal, lunch`), 공백·중복은 자동 정리 |

### 첫 실행 동작 (안 A: 기본 카테고리 자동 생성)
데이터 폴더나 파일이 없으면 자동으로 만들고 안내 메시지를 출력합니다.
카테고리 파일이 비어 있으면 기본 카테고리 `food, transport, rent, salary, shopping, etc`를 등록합니다.

```text
$ python -m budget_app list
[안내] 데이터 폴더를 새로 만들었습니다: C:\...\data
[안내] 기본 카테고리를 등록했습니다: food, transport, rent, salary, shopping, etc
거래 내역이 없습니다. 'add' 명령으로 첫 거래를 추가해 보세요.
```

---

## 3. 주요 명령 예시

### 거래 추가 (대화형)
잘못 입력하면 오류와 힌트를 보여주고 같은 항목을 다시 묻습니다. 날짜에서 엔터만 누르면 오늘 날짜가 들어갑니다.

```text
$ python -m budget_app add
등록된 카테고리: food, transport, rent, salary, shopping, etc
날짜(YYYY-MM-DD, 엔터=2024-01-15): 2024-13-40
[오류] 날짜 형식이 올바르지 않습니다 (YYYY-MM-DD): '2024-13-40'
[힌트] 예: 2024-01-15 (실제로 존재하는 날짜여야 합니다)
날짜(YYYY-MM-DD, 엔터=2024-01-15): 2024-01-15
타입(income/expense): expense
카테고리: food
금액(양수): 15000
메모(선택): 점심
태그(쉼표로 구분, 없으면 엔터): meal
[저장 완료] id=TX-000012
```

### 목록 / 검색
```text
$ python -m budget_app list --limit 3
ID        | 날짜       | 타입    | 카테고리  |      금액 | 메모 | 태그
----------+------------+---------+-----------+-----------+------+-----
TX-000012 | 2024-01-15 | expense | food      |    15,000 | 점심 | meal
TX-000011 | 2024-01-14 | income  | salary    | 3,000,000 |      |
TX-000010 | 2024-01-12 | expense | transport |    20,000 |      |

최신순 3건 (--limit 3)

$ python -m budget_app search --from 2024-01-01 --to 2024-01-31 --type expense
$ python -m budget_app search --category food --q 점심 --tag meal
$ python -m budget_app search --month 2024-01 --limit 20
```

`list` 기본값은 `--limit 10`, `search`는 기본적으로 조건에 맞는 전체를 보여줍니다.
`--q`는 메모 부분 일치, `--tag`는 태그 정확히 일치이며 둘 다 대소문자를 구분하지 않습니다.

### 예산 + 월별 요약
```text
$ python -m budget_app budget set --month 2024-01 --amount 500000
[저장 완료] 2024-01 예산 500,000원

$ python -m budget_app summary --month 2024-01 --top 3
[2024-01 월별 요약]
거래 수: 5건
총 수입: 3,000,000원
총 지출: 215,000원
잔액: 2,785,000원
예산: 500,000원 [########............] 사용률 43.0%
남은 예산: 285,000원

지출 TOP 3
 1) rent       150,000원  (69.8%)
 2) food        45,000원  (20.9%)
 3) transport   20,000원  (9.3%)
```

예산을 넘은 경우:
```text
예산: 200,000원 [####################] 사용률 115.0%
[경고] 예산을 30,000원 초과했습니다! 지출을 점검하세요.
```

- 예산을 넘으면 `[경고] 예산을 15,000원 초과했습니다!`, 80% 이상이면 `[주의]` 문구가 나옵니다.
- 거래가 없는 달은 `데이터 없음: 2024-03에 기록된 거래가 없습니다.`를 출력합니다.
- `--month`를 생략하면 이번 달, `--top` 기본값은 5입니다.
- `budget get --month 2024-01`, `budget list`로 예산만 조회할 수도 있습니다.

### 카테고리
```text
$ python -m budget_app category add
카테고리명: 용돈
[저장 완료] category=용돈

$ python -m budget_app category list
카테고리  | 사용 거래
----------+----------
food      |       3건
transport |       1건
...

$ python -m budget_app category remove --name shopping
[오류] 'shopping' 카테고리를 사용하는 거래 2건, 반복 규칙 0건이 있어 삭제할 수 없습니다.
[힌트] 대체 카테고리를 지정하세요: category remove --name shopping --replace-with etc

$ python -m budget_app category remove --name shopping --replace-with etc
[삭제 완료] category=shopping (거래 2건을 'etc'(으)로 이동)
```

사용 중인 카테고리는 **삭제를 막고**, `--replace-with`로 대체 카테고리를 지정하면 거래를 옮긴 뒤 삭제합니다.
마지막 남은 카테고리는 삭제할 수 없습니다.

### 수정 / 삭제
`update`는 **옵션 방식(안 A)으로 고정**했습니다. 지정한 필드만 바뀌고 나머지는 그대로 유지됩니다.

```text
$ python -m budget_app update --id TX-000012 --amount 12000 --memo 저녁
[수정 완료] id=TX-000012
  수정 전: TX-000012 | 2024-01-15 | expense | food | 15,000원 | 점심
  수정 후: TX-000012 | 2024-01-15 | expense | food | 12,000원 | 저녁

$ python -m budget_app update --id 12 --tags ""          # 태그 비우기 (--memo "" 는 메모 비우기)

$ python -m budget_app delete --id TX-000012
[삭제 완료] TX-000012 | 2024-01-15 | expense | food | 12,000원 | 저녁

$ python -m budget_app delete --id TX-999999
[오류] 해당 id의 거래가 없습니다: TX-999999
[힌트] 'list' 또는 'search' 명령으로 id를 확인하세요.
```

수정 가능한 옵션: `--date`, `--type`, `--category`, `--amount`, `--memo`, `--tags`

### 가져오기 / 내보내기
```text
$ python -m budget_app import --from samples/import_sample.csv
[완료] imported=5, skipped=0

$ python -m budget_app export --out export.csv --month 2024-01
[완료] export.csv (12 records)

$ python -m budget_app export --out q1.csv --from 2024-01-01 --to 2024-03-31 --type expense
```

- `import`: 잘못된 행은 건너뛰고 행 번호와 사유를 보여줍니다. `--dry-run`은 저장 없이 검증만, `--create-categories`는 없는 카테고리를 자동 등록합니다.
- `export`: `--month` 또는 `--from`/`--to` 중 **하나 이상 필수**입니다. `--category`, `--type`, `--q`, `--tag`로 더 좁힐 수 있습니다. 같은 이름의 파일이 있으면 `--force` 없이는 덮어쓰지 않습니다.

---

## 4. import / export CSV 스키마

공통: **UTF-8, 첫 줄 헤더 포함**. (엑셀이 붙이는 BOM이 있어도 import 가능)

| column | required | 설명 |
| --- | --- | --- |
| `date` | Y | `YYYY-MM-DD` |
| `type` | Y | `income` / `expense` |
| `category` | Y | 등록된 카테고리 |
| `amount` | Y | 양수 정수 |
| `memo` | N | 문자열 |
| `tags` | N | 쉼표(`,`) 구분 문자열. CSV 안에서는 따옴표로 감쌉니다 (`"meal,work"`) |

```csv
date,type,category,amount,memo,tags
2024-01-05,income,salary,3000000,1월 월급,
2024-01-10,expense,rent,150000,월세,"house,fixed"
2024-01-15,expense,food,15000,점심,meal
```

- export 파일은 위 6개 컬럼을 이 순서로 씁니다. 그래서 export한 파일을 그대로 import할 수 있습니다.
- import는 헤더 이름으로 컬럼을 찾으므로 순서가 달라도 되고, 추가 컬럼(예: `id`)은 무시합니다. id는 새로 발급됩니다.
- 예제 파일: [samples/import_sample.csv](samples/import_sample.csv)

---

## 5. 보너스 기능

### 백업 / 복원
```text
$ python -m budget_app backup
[백업 완료] data\backups\backup-20240115-093000.zip

$ python -m budget_app backup list
$ python -m budget_app backup restore --file backup-20240115-093000.zip
[복원 완료] backup-20240115-093000.zip
복원 직전 데이터는 data\backups\backup-20240115-094512.zip 에 백업해 두었습니다.
```
복원 전에 백업 내용이 올바른 JSONL인지 검사하고, 현재 데이터를 먼저 자동 백업합니다.

### 반복 거래
```text
$ python -m budget_app recurring add --day 25 --type income --category salary --amount 3000000 --memo 월급
[저장 완료] id=RC-000001 (매월 25일 income salary 3,000,000원)

$ python -m budget_app recurring apply --month 2024-02
[완료] 2024-02 반복 거래 created=1, skipped=0
  + TX-000013 | 2024-02-25 | income | salary | 3,000,000원 | 월급
```
- 같은 달에 다시 실행해도 이미 만든 규칙은 건너뜁니다. 생성된 거래에 `recurring_id`가 기록되기 때문입니다.
- `--day 31`이면 2월에는 말일(28/29일)로 생성합니다.

### 표 정렬
외부 라이브러리 없이 `unicodedata.east_asian_width`로 한글(2칸) 폭을 계산해 열을 맞춥니다. ([formatter.py](budget_app/formatter.py))

### 저장 원자성
수정/삭제/예산 설정/카테고리 삭제/export는 같은 폴더의 임시 파일에 쓰고, `fsync` 뒤 `os.replace`로 교체합니다.
도중에 실패하면 임시 파일만 지워지고 원본은 그대로 남습니다. ([storage.py](budget_app/storage.py) `atomic_open`)

---

## 6. 오류 처리와 종료 코드

```text
$ python -m budget_app summary --month 2024-13
[오류] 월 형식이 올바르지 않습니다 (YYYY-MM): '2024-13'
[힌트] 예: 2024-01
```

| 종료 코드 | 의미 |
| --- | --- |
| 0 | 정상 종료 |
| 1 | 입력 검증 실패, 충돌(이미 존재, 사용 중인 카테고리 등) |
| 2 | 명령/옵션 사용법 오류 |
| 3 | 대상 없음(없는 id, 없는 파일) |
| 4 | 저장 파일 손상, 파일 읽기/쓰기 실패 |
| 10 | 예상하지 못한 오류(상세 내용은 `data/app.log`에만 기록) |
| 130 | Ctrl+C / 입력 종료로 취소 |

저장 파일이 손상되면 몇 번째 줄인지 알려주고, 손상된 파일을 덮어쓰지 않습니다.

---

## 7. 프로젝트 구조와 설계

```text
budget_app/
├── __main__.py    # python -m budget_app 진입점
├── cli.py         # CLI 계층: argparse 파서, 대화형 입력, 출력
├── services.py    # 서비스 계층: 규칙/계산 (Ledger, Category, Budget, Recurring, Backup)
├── storage.py     # 저장소 계층: JSONL/CSV 파일 입출력, 스트리밍, 원자적 쓰기
├── models.py      # 모델 계층: dataclass (Transaction, Budget, TransactionFilter, MonthlySummary ...)
├── validators.py  # 입력 검증 (대화형/옵션/CSV가 같은 규칙 사용)
├── decorators.py  # 공통 관심사: handle_errors / log_call / timed
├── formatter.py   # 콘솔 출력 문자열(표 정렬, 요약 리포트)
└── errors.py      # 원인+힌트를 가진 예외와 종료 코드
tests/
└── test_budget_app.py
samples/
└── import_sample.csv
```

### 계층별 책임
| 계층 | 하는 일 | 하지 않는 일 |
| --- | --- | --- |
| CLI (`cli.py`) | 옵션 해석, `input()`, `print()` | 계산, 파일 접근 |
| 서비스 (`services.py`) | 카테고리 존재 확인, 요약 계산, import 검증 같은 규칙 | 콘솔 입출력 |
| 저장소 (`storage.py`) | 파일 읽기/쓰기, 스트리밍, 원자적 교체 | 업무 규칙 판단 |
| 모델 (`models.py`) | 데이터 구조와 순수 계산(필터 일치, 잔액, 사용률) | I/O |

예를 들어 저장 형식을 SQLite로 바꾸려면 `storage.py`만 교체하면 되고, 웹 화면을 붙이려면 `cli.py` 대신 다른 진입점에서 같은 서비스를 호출하면 됩니다.

### 제너레이터 스트리밍
```python
# storage.py
def iter_parsed(self, parse):
    with self.path.open("r", encoding="utf-8") as file:
        for line_no, line in enumerate(file, start=1):
            ...
            yield item            # 한 줄씩 넘겨준다
```
- `list`: `heapq.nlargest(limit, repo.stream(), key=...)` → 파일을 끝까지 훑어도 메모리에는 **limit건만** 유지합니다.
- `search`: 조건에 맞는 거래만 모은 뒤 정렬합니다. 파일 전체가 아니라 결과만 메모리에 올라갑니다.
- `summary`: 해당 월을 한 번 훑으며 합계와 `Counter`만 누적합니다.
- `export`: 읽은 거래를 바로 CSV에 씁니다. 메모리 사용량이 파일 크기와 무관합니다.
- `update/delete`: 원본을 스트리밍으로 읽으며 변환한 결과를 임시 파일에 바로 쓰고 교체합니다.

### 데코레이터
| 데코레이터 | 적용 위치 | 역할 |
| --- | --- | --- |
| `@handle_errors` | `cli.run` | 모든 예외를 `[오류]/[힌트]`로 바꾸고 종료 코드를 돌려줌. 스택트레이스는 화면에 출력하지 않음 |
| `@log_call` | 서비스의 변경 작업(add/update/delete/import ...) | 시작/완료/실패를 `app.log`에 기록 |
| `@timed` | 조회·집계·입출력(recent/search/summary/import/export ...) | 실행 시간을 기록 (`--verbose`면 화면에도 표시) |

서비스 코드에는 try/except, 로그, 시간 측정 코드가 없어서 업무 로직만 읽으면 됩니다.

### 타입 힌트
- `TxType = Literal["income", "expense"]`로 허용 값을 타입에 명시했습니다.
- `TransactionDict(TypedDict)`로 JSONL 한 줄의 구조를 문서화했습니다.
- `Iterator[Transaction]` 반환 타입으로 "한 번에 하나씩 흘려보내는 함수"임을 시그니처에서 알 수 있습니다.
- 데코레이터는 `ParamSpec`/`TypeVar`로 감싼 함수의 인자·반환 타입을 그대로 유지합니다.
- `TransactionPatch`의 `None` 필드는 "변경 없음"을 뜻해, 빈 문자열(값 지우기)과 구분됩니다.

---

## 8. 테스트

```bash
python -m unittest discover -s tests -v
```

임시 폴더에서 실제 CLI(`main()`)를 실행해 추가/재입력/검색/요약/예산 경고/수정·삭제/카테고리 삭제 정책/
import·export 왕복/파일 손상 감지/백업·복원/반복 거래 중복 방지를 검증합니다.
