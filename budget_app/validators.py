"""입력 검증: 문자열 입력을 검증된 값으로 바꾸거나 ValidationError를 던진다.

대화형 입력, 옵션 인자, CSV import가 모두 같은 함수를 사용하므로 규칙이 한 곳에 모인다.
"""

from __future__ import annotations

import re
from datetime import datetime

from .errors import ValidationError
from .models import TX_TYPES, TxType, format_id

MAX_AMOUNT = 1_000_000_000_000   # 1조 원
MAX_MEMO_LENGTH = 200
MAX_CATEGORY_LENGTH = 30

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_MONTH_RE = re.compile(r"\d{4}-\d{2}")
_TYPE_ALIASES: dict[str, TxType] = {"수입": "income", "지출": "expense"}


def parse_date(value: str) -> str:
    text = value.strip()
    if _DATE_RE.fullmatch(text):
        try:
            return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
        except ValueError:
            pass
    raise ValidationError(
        f"날짜 형식이 올바르지 않습니다 (YYYY-MM-DD): '{text}'",
        "예: 2024-01-15 (실제로 존재하는 날짜여야 합니다)",
    )


def parse_month(value: str) -> str:
    text = value.strip()
    if _MONTH_RE.fullmatch(text):
        try:
            return datetime.strptime(text, "%Y-%m").strftime("%Y-%m")
        except ValueError:
            pass
    raise ValidationError(f"월 형식이 올바르지 않습니다 (YYYY-MM): '{text}'", "예: 2024-01")


def parse_period(date_from: str | None, date_to: str | None) -> tuple[str | None, str | None]:
    start = parse_date(date_from) if date_from else None
    end = parse_date(date_to) if date_to else None
    if start and end and start > end:
        raise ValidationError(
            f"시작일이 종료일보다 늦습니다: {start} > {end}",
            "--from 에는 더 이른 날짜, --to 에는 더 늦은 날짜를 지정하세요.",
        )
    return start, end


def parse_amount(value: str | int) -> int:
    text = str(value).strip().replace(",", "").removesuffix("원").strip()
    try:
        amount = int(text)
    except ValueError:
        raise ValidationError(
            f"금액은 정수로 입력해야 합니다: '{value}'", "예: 15000 또는 15,000 (소수점 불가)"
        ) from None
    if amount <= 0:
        raise ValidationError(
            f"금액은 0보다 큰 양수여야 합니다: {amount}",
            "지출도 양수로 입력하고, 수입/지출은 타입(income/expense)으로 구분합니다.",
        )
    if amount > MAX_AMOUNT:
        raise ValidationError(f"금액이 너무 큽니다: {amount:,}", f"{MAX_AMOUNT:,}원 이하로 입력하세요.")
    return amount


def parse_type(value: str) -> TxType:
    text = value.strip().lower()
    if text in _TYPE_ALIASES:
        return _TYPE_ALIASES[text]
    for tx_type in TX_TYPES:
        if text == tx_type:
            return tx_type
    raise ValidationError(
        f"허용되지 않은 타입입니다: '{value.strip()}'", "income(수입) 또는 expense(지출) 중 하나를 입력하세요."
    )


def parse_category_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise ValidationError("카테고리 이름이 비어 있습니다.", "예: food, transport, 용돈")
    if len(name) > MAX_CATEGORY_LENGTH:
        raise ValidationError(
            f"카테고리 이름이 너무 깁니다 ({len(name)}자).", f"{MAX_CATEGORY_LENGTH}자 이하로 입력하세요."
        )
    return name


def parse_memo(value: str) -> str:
    memo = " ".join(value.split())   # 줄바꿈/연속 공백 정리
    if len(memo) > MAX_MEMO_LENGTH:
        raise ValidationError(
            f"메모가 너무 깁니다 ({len(memo)}자).", f"{MAX_MEMO_LENGTH}자 이하로 입력하세요."
        )
    return memo


def parse_tags(value: str) -> list[str]:
    """'meal, 회식,meal' → ['meal', '회식'] (공백 제거, 빈 값/중복 제거)"""
    tags: list[str] = []
    for raw in value.split(","):
        tag = raw.strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def parse_day(value: str | int) -> int:
    try:
        day = int(str(value).strip())
    except ValueError:
        day = 0
    if not 1 <= day <= 31:
        raise ValidationError(
            f"날짜(일)는 1~31 사이 정수여야 합니다: '{value}'", "예: 25 (해당 월에 없는 날이면 말일로 생성됩니다)"
        )
    return day


def parse_positive_int(value: int, option: str) -> int:
    if value <= 0:
        raise ValidationError(f"{option} 값은 1 이상이어야 합니다: {value}", f"예: {option} 10")
    return value


def parse_id(value: str, prefix: str) -> str:
    """'TX-12', 'tx-000012', '12' → 'TX-000012'"""
    text = value.strip().upper()
    digits = text.removeprefix(f"{prefix}-")
    if not digits.isdigit():
        raise ValidationError(
            f"id 형식이 올바르지 않습니다: '{value.strip()}'",
            f"예: {format_id(prefix, 12)} 또는 숫자만 12",
        )
    return format_id(prefix, int(digits))
