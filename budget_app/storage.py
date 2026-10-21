"""저장소 계층: JSONL/CSV 파일 입출력만 책임진다.

- 읽기는 항상 제너레이터로 한 줄씩 스트리밍한다. 파일 전체를 메모리에 올리지 않는다.
- 추가는 파일 끝에 append 한다.
- 수정/삭제는 임시 파일에 새로 쓴 뒤 os.replace로 원본과 원자적으로 교체한다.
  쓰는 도중 프로그램이 죽어도 원본 파일은 이전 상태 그대로 남는다.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, TextIO, TypeVar

from .errors import NotFoundError, StorageError, ValidationError
from .models import (
    RULE_ID_PREFIX,
    TX_ID_PREFIX,
    TX_TYPES,
    Budget,
    RecurringRule,
    Transaction,
    TransactionFilter,
    format_id,
    id_number,
)

Record = dict[str, Any]
T = TypeVar("T")

TRANSACTIONS_FILE = "transactions.jsonl"
CATEGORIES_FILE = "categories.jsonl"
BUDGETS_FILE = "budgets.jsonl"
RECURRING_FILE = "recurring.jsonl"
DATA_FILES: tuple[str, ...] = (TRANSACTIONS_FILE, CATEGORIES_FILE, BUDGETS_FILE, RECURRING_FILE)

DEFAULT_CATEGORIES: tuple[str, ...] = ("food", "transport", "rent", "salary", "shopping", "etc")

CSV_COLUMNS: tuple[str, ...] = ("date", "type", "category", "amount", "memo", "tags")
CSV_REQUIRED_COLUMNS: tuple[str, ...] = ("date", "type", "category", "amount")


@contextmanager
def atomic_open(path: Path, newline: str = "\n") -> Iterator[TextIO]:
    """같은 폴더의 임시 파일에 쓰고, 성공했을 때만 대상 파일과 교체한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as file:
            yield file
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _dumps(record: Record) -> str:
    return json.dumps(record, ensure_ascii=False) + "\n"


class JsonlFile:
    """JSON Lines 파일 하나(한 줄 = JSON 객체 하나)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def ensure_exists(self) -> bool:
        """파일이 없으면 빈 파일을 만든다. 새로 만들었으면 True."""
        if self.path.exists():
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch()
        return True

    def iter_parsed(self, parse: Callable[[Record], T]) -> Iterator[T]:
        """한 줄씩 읽어 parse 결과를 yield 한다(스트리밍)."""
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as file:
            for line_no, line in enumerate(file, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError("JSON 객체가 아님")
                    item = parse(record)
                except (ValueError, KeyError, TypeError):
                    raise StorageError(
                        f"저장 파일이 손상되었습니다: {self.path} ({line_no}번째 줄)",
                        "해당 줄을 직접 고치거나 'backup restore --file <백업파일>'로 복구하세요.",
                    ) from None
                yield item

    def append(self, records: Iterable[Record]) -> int:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        needs_newline = self._ends_without_newline()
        count = 0
        with self.path.open("a", encoding="utf-8", newline="\n") as file:
            if needs_newline:   # 사람이 직접 편집해 마지막 줄바꿈이 없는 경우 줄이 붙지 않도록
                file.write("\n")
            for record in records:
                file.write(_dumps(record))
                count += 1
            file.flush()
            os.fsync(file.fileno())
        return count

    def rewrite(self, records: Iterable[Record]) -> int:
        """records로 파일 전체를 원자적으로 교체한다. records는 이 파일을 읽는 제너레이터여도 된다."""
        count = 0
        with atomic_open(self.path) as file:
            for record in records:
                file.write(_dumps(record))
                count += 1
        return count

    def _ends_without_newline(self) -> bool:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return False
        with self.path.open("rb") as file:
            file.seek(-1, os.SEEK_END)
            return file.read(1) != b"\n"


def _parse_transaction(record: Record) -> Transaction:
    tx = Transaction.from_dict(record)
    if tx.type not in TX_TYPES or tx.amount <= 0:
        raise ValueError("잘못된 거래 데이터")
    return tx


class TransactionRepository:
    """거래 내역 저장소(transactions.jsonl)."""

    def __init__(self, path: Path) -> None:
        self._file = JsonlFile(path)

    @property
    def path(self) -> Path:
        return self._file.path

    def ensure_exists(self) -> bool:
        return self._file.ensure_exists()

    def stream(self) -> Iterator[Transaction]:
        """저장 순서대로 거래를 하나씩 yield 한다. 메모리에는 한 건만 올라간다."""
        yield from self._file.iter_parsed(_parse_transaction)

    def find(self, condition: TransactionFilter) -> Iterator[Transaction]:
        return (tx for tx in self.stream() if condition.matches(tx))

    def get(self, tx_id: str) -> Transaction | None:
        return next((tx for tx in self.stream() if tx.id == tx_id), None)

    def next_id(self) -> str:
        last = max((id_number(tx.id) for tx in self.stream()), default=0)
        return format_id(TX_ID_PREFIX, last + 1)

    def add(self, tx: Transaction) -> None:
        self._file.append([tx.to_dict()])

    def add_many(self, transactions: Iterable[Transaction]) -> int:
        return self._file.append(tx.to_dict() for tx in transactions)

    def replace(self, updated: Transaction) -> bool:
        return self._rewrite(lambda tx: updated if tx.id == updated.id else tx) > 0

    def delete(self, tx_id: str) -> bool:
        return self._rewrite(lambda tx: None if tx.id == tx_id else tx) > 0

    def reassign_category(self, old: str, new: str) -> int:
        return self._rewrite(lambda tx: replace(tx, category=new) if tx.category == old else tx)

    def _rewrite(self, transform: Callable[[Transaction], Transaction | None]) -> int:
        """원본을 스트리밍으로 읽으며 변환한 결과를 임시 파일에 쓰고 교체한다.

        transform이 None을 돌려주면 삭제, 다른 객체를 돌려주면 수정으로 본다.
        변경된 건수를 돌려준다.
        """
        changed = 0

        def transformed() -> Iterator[Record]:
            nonlocal changed
            for tx in self.stream():
                result = transform(tx)
                if result is not tx:
                    changed += 1
                if result is not None:
                    yield result.to_dict()

        self._file.rewrite(transformed())
        return changed


class CategoryStore:
    """카테고리 목록 저장소(categories.jsonl). 줄 형식: {"name": "food"}"""

    def __init__(self, path: Path) -> None:
        self._file = JsonlFile(path)

    def ensure_defaults(self) -> bool:
        """파일이 없거나 비어 있으면 기본 카테고리를 만든다. 만들었으면 True."""
        self._file.ensure_exists()
        if self.names():
            return False
        self._file.rewrite({"name": name} for name in DEFAULT_CATEGORIES)
        return True

    def names(self) -> list[str]:
        return list(self._file.iter_parsed(lambda record: str(record["name"])))

    def exists(self, name: str) -> bool:
        return name in self.names()

    def add(self, name: str) -> None:
        self._file.append([{"name": name}])

    def remove(self, name: str) -> None:
        remaining = [n for n in self.names() if n != name]
        self._file.rewrite({"name": n} for n in remaining)


class BudgetStore:
    """월 예산 저장소(budgets.jsonl). 줄 형식: {"month": "2024-01", "amount": 500000}"""

    def __init__(self, path: Path) -> None:
        self._file = JsonlFile(path)

    def ensure_exists(self) -> bool:
        return self._file.ensure_exists()

    def all(self) -> list[Budget]:
        return list(self._file.iter_parsed(Budget.from_dict))

    def get(self, month: str) -> Budget | None:
        return next((b for b in self._file.iter_parsed(Budget.from_dict) if b.month == month), None)

    def set(self, budget: Budget) -> None:
        """같은 달 예산이 있으면 덮어쓴다(upsert)."""
        budgets = [b for b in self.all() if b.month != budget.month]
        budgets.append(budget)
        budgets.sort(key=lambda b: b.month)
        self._file.rewrite(b.to_dict() for b in budgets)


class RecurringStore:
    """반복 거래 규칙 저장소(recurring.jsonl)."""

    def __init__(self, path: Path) -> None:
        self._file = JsonlFile(path)

    def ensure_exists(self) -> bool:
        return self._file.ensure_exists()

    def all(self) -> list[RecurringRule]:
        return list(self._file.iter_parsed(RecurringRule.from_dict))

    def next_id(self) -> str:
        last = max((id_number(rule.id) for rule in self.all()), default=0)
        return format_id(RULE_ID_PREFIX, last + 1)

    def add(self, rule: RecurringRule) -> None:
        self._file.append([rule.to_dict()])

    def remove(self, rule_id: str) -> None:
        self._file.rewrite(rule.to_dict() for rule in self.all() if rule.id != rule_id)

    def reassign_category(self, old: str, new: str) -> int:
        rules = self.all()
        changed = 0
        for rule in rules:
            if rule.category == old:
                rule.category = new
                changed += 1
        if changed:
            self._file.rewrite(rule.to_dict() for rule in rules)
        return changed


class Storage:
    """데이터 폴더 하나에 속한 모든 저장소 묶음."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.transactions = TransactionRepository(data_dir / TRANSACTIONS_FILE)
        self.categories = CategoryStore(data_dir / CATEGORIES_FILE)
        self.budgets = BudgetStore(data_dir / BUDGETS_FILE)
        self.recurring = RecurringStore(data_dir / RECURRING_FILE)

    def initialize(self) -> list[str]:
        """첫 실행이면 폴더/파일/기본 카테고리를 만들고, 사용자에게 보여줄 안내 문구를 돌려준다."""
        if self.data_dir.exists() and not self.data_dir.is_dir():
            raise StorageError(
                f"데이터 경로가 폴더가 아니라 파일입니다: {self.data_dir}",
                "--data-dir 옵션으로 다른 폴더를 지정하세요.",
            )
        notices: list[str] = []
        if not self.data_dir.exists():
            self.data_dir.mkdir(parents=True)
            notices.append(f"데이터 폴더를 새로 만들었습니다: {self.data_dir.resolve()}")
        self.transactions.ensure_exists()
        self.budgets.ensure_exists()
        self.recurring.ensure_exists()
        if self.categories.ensure_defaults():
            notices.append(f"기본 카테고리를 등록했습니다: {', '.join(DEFAULT_CATEGORIES)}")
        return notices


def iter_csv_rows(path: Path) -> Iterator[tuple[int, dict[str, str]]]:
    """CSV를 한 행씩 (줄 번호, {컬럼: 값}) 으로 yield 한다. 헤더는 소문자로 맞춘다."""
    if not path.is_file():
        raise NotFoundError(f"CSV 파일을 찾을 수 없습니다: {path}", "경로와 파일 이름을 확인하세요.")
    # utf-8-sig: 엑셀이 붙이는 BOM이 있어도 첫 컬럼 이름이 깨지지 않도록 한다.
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        header = [name.strip().lower() for name in reader.fieldnames or []]
        missing = [column for column in CSV_REQUIRED_COLUMNS if column not in header]
        if missing:
            raise ValidationError(
                f"CSV 헤더에 필수 컬럼이 없습니다: {', '.join(missing)}",
                f"첫 줄(헤더)은 다음과 같아야 합니다: {','.join(CSV_COLUMNS)}",
            )
        reader.fieldnames = header
        for row in reader:
            values = {key: (value or "").strip() for key, value in row.items() if isinstance(key, str)}
            yield reader.line_num, values


def write_csv(path: Path, transactions: Iterable[Transaction]) -> int:
    """거래를 CSV로 원자적으로 저장하고 기록한 건수를 돌려준다."""
    count = 0
    with atomic_open(path, newline="") as file:
        writer = csv.writer(file)
        writer.writerow(CSV_COLUMNS)
        for tx in transactions:
            writer.writerow([tx.date, tx.type, tx.category, tx.amount, tx.memo, ",".join(tx.tags)])
            count += 1
    return count
