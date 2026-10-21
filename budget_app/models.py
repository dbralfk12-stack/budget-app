"""모델 계층: 가계부에서 다루는 데이터 구조를 정의한다.

이 모듈은 파일 I/O, 콘솔 출력에 의존하지 않는 순수한 데이터 구조와 계산만 담는다.
"""

from __future__ import annotations

import calendar
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal, TypedDict

TxType = Literal["income", "expense"]
TX_TYPES: tuple[TxType, ...] = ("income", "expense")

TX_ID_PREFIX = "TX"
RULE_ID_PREFIX = "RC"


def format_id(prefix: str, number: int) -> str:
    """(TX, 12) → 'TX-000012'"""
    return f"{prefix}-{number:06d}"


def id_number(identifier: str) -> int:
    """'TX-000012' → 12. 형식이 다르면 0."""
    _, _, digits = identifier.rpartition("-")
    return int(digits) if digits.isdigit() else 0


def month_bounds(month: str) -> tuple[str, str]:
    """'2024-02' → ('2024-02-01', '2024-02-29')"""
    year, mon = (int(part) for part in month.split("-"))
    last_day = calendar.monthrange(year, mon)[1]
    return f"{month}-01", f"{month}-{last_day:02d}"


class TransactionDict(TypedDict, total=False):
    """transactions.jsonl 한 줄의 JSON 구조."""

    id: str
    type: TxType
    date: str
    amount: int
    category: str
    memo: str
    tags: list[str]
    recurring_id: str


@dataclass(slots=True)
class Transaction:
    """저장된 거래 1건."""

    id: str
    type: TxType
    date: str                       # YYYY-MM-DD
    amount: int                     # 양수 정수(원)
    category: str
    memo: str = ""
    tags: list[str] = field(default_factory=list)
    recurring_id: str | None = None  # 반복 규칙으로 자동 생성된 거래면 규칙 id

    @property
    def month(self) -> str:
        return self.date[:7]

    def sort_key(self) -> tuple[str, int]:
        """최신순 정렬 키: 날짜가 같으면 나중에 등록된(id가 큰) 거래가 더 최신."""
        return (self.date, id_number(self.id))

    def to_dict(self) -> TransactionDict:
        data: TransactionDict = {
            "id": self.id,
            "type": self.type,
            "date": self.date,
            "amount": self.amount,
            "category": self.category,
            "memo": self.memo,
            "tags": list(self.tags),
        }
        if self.recurring_id:
            data["recurring_id"] = self.recurring_id
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Transaction:
        return cls(
            id=str(data["id"]),
            type=data["type"],
            date=str(data["date"]),
            amount=int(data["amount"]),
            category=str(data["category"]),
            memo=str(data.get("memo") or ""),
            tags=[str(tag) for tag in data.get("tags") or []],
            recurring_id=data.get("recurring_id"),
        )


@dataclass(slots=True)
class TransactionDraft:
    """id가 아직 없는, 검증을 마친 신규 거래 입력값."""

    type: TxType
    date: str
    amount: int
    category: str
    memo: str = ""
    tags: list[str] = field(default_factory=list)

    def to_transaction(self, tx_id: str, recurring_id: str | None = None) -> Transaction:
        return Transaction(
            id=tx_id,
            type=self.type,
            date=self.date,
            amount=self.amount,
            category=self.category,
            memo=self.memo,
            tags=list(self.tags),
            recurring_id=recurring_id,
        )


@dataclass(slots=True, frozen=True)
class TransactionPatch:
    """update 명령에서 바꿀 필드만 담는다. None인 필드는 그대로 둔다."""

    date: str | None = None
    type: TxType | None = None
    category: str | None = None
    amount: int | None = None
    memo: str | None = None
    tags: list[str] | None = None

    def changes(self) -> dict[str, Any]:
        candidates = {
            "date": self.date,
            "type": self.type,
            "category": self.category,
            "amount": self.amount,
            "memo": self.memo,
            "tags": self.tags,
        }
        return {name: value for name, value in candidates.items() if value is not None}

    def is_empty(self) -> bool:
        return not self.changes()

    def apply(self, tx: Transaction) -> Transaction:
        return replace(tx, **self.changes())


@dataclass(slots=True, frozen=True)
class TransactionFilter:
    """search/export/summary에서 쓰는 조건. 지정하지 않은(None) 조건은 무시한다."""

    date_from: str | None = None
    date_to: str | None = None
    category: str | None = None
    type: TxType | None = None
    keyword: str | None = None   # 메모 부분 일치(대소문자 무시)
    tag: str | None = None       # 태그 정확히 일치(대소문자 무시)

    @classmethod
    def for_month(cls, month: str) -> TransactionFilter:
        date_from, date_to = month_bounds(month)
        return cls(date_from=date_from, date_to=date_to)

    def matches(self, tx: Transaction) -> bool:
        if self.date_from and tx.date < self.date_from:
            return False
        if self.date_to and tx.date > self.date_to:
            return False
        if self.category and tx.category != self.category:
            return False
        if self.type and tx.type != self.type:
            return False
        if self.keyword and self.keyword.casefold() not in tx.memo.casefold():
            return False
        if self.tag and self.tag.casefold() not in (tag.casefold() for tag in tx.tags):
            return False
        return True


@dataclass(slots=True, frozen=True)
class Budget:
    """월 예산."""

    month: str   # YYYY-MM
    amount: int

    def to_dict(self) -> dict[str, Any]:
        return {"month": self.month, "amount": self.amount}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Budget:
        return cls(month=str(data["month"]), amount=int(data["amount"]))


@dataclass(slots=True)
class RecurringRule:
    """매달 같은 날 반복되는 거래(월급, 월세 등) 규칙."""

    id: str
    day: int     # 1~31, 해당 월에 없는 날짜면 말일로 생성
    type: TxType
    category: str
    amount: int
    memo: str = ""
    tags: list[str] = field(default_factory=list)

    def date_in(self, month: str) -> str:
        _, last = month_bounds(month)
        last_day = int(last[-2:])
        return f"{month}-{min(self.day, last_day):02d}"

    def to_draft(self, month: str) -> TransactionDraft:
        return TransactionDraft(
            type=self.type,
            date=self.date_in(month),
            amount=self.amount,
            category=self.category,
            memo=self.memo,
            tags=list(self.tags),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "day": self.day,
            "type": self.type,
            "category": self.category,
            "amount": self.amount,
            "memo": self.memo,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RecurringRule:
        return cls(
            id=str(data["id"]),
            day=int(data["day"]),
            type=data["type"],
            category=str(data["category"]),
            amount=int(data["amount"]),
            memo=str(data.get("memo") or ""),
            tags=[str(tag) for tag in data.get("tags") or []],
        )


@dataclass(slots=True)
class MonthlySummary:
    """summary 명령의 계산 결과."""

    month: str
    income: int = 0
    expense: int = 0
    count: int = 0
    top_expenses: list[tuple[str, int]] = field(default_factory=list)
    budget: int | None = None

    @property
    def balance(self) -> int:
        return self.income - self.expense

    @property
    def has_data(self) -> bool:
        return self.count > 0

    @property
    def usage_rate(self) -> float | None:
        """예산 대비 지출 비율(%). 예산이 없으면 None."""
        if not self.budget:
            return None
        return self.expense / self.budget * 100

    @property
    def over_budget(self) -> bool:
        return self.budget is not None and self.expense > self.budget


@dataclass(slots=True)
class ImportResult:
    """import 명령의 처리 결과."""

    imported: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    created_categories: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ApplyResult:
    """recurring apply 명령의 처리 결과."""

    created: list[Transaction] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
