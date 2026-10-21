"""출력 계층: 콘솔에 보여줄 문자열을 만든다.

외부 라이브러리 없이 한글(전각 문자) 폭까지 계산해 표를 정렬한다.
"""

from __future__ import annotations

import sys
import unicodedata
from collections.abc import Sequence

from .models import Budget, MonthlySummary, RecurringRule, Transaction

LEFT = "<"
RIGHT = ">"
WARNING_RATE = 80.0   # 예산 사용률이 이 값(%) 이상이면 주의 문구 출력


def display_width(text: str) -> int:
    """콘솔에서 차지하는 칸 수. 한글 등 전각 문자는 2칸."""
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def truncate(text: str, max_width: int) -> str:
    if display_width(text) <= max_width:
        return text
    kept: list[str] = []
    width = 0
    for char in text:
        char_width = display_width(char)
        if width + char_width > max_width - 2:
            break
        kept.append(char)
        width += char_width
    return "".join(kept) + ".."


def pad(text: str, width: int, align: str = LEFT) -> str:
    gap = " " * max(width - display_width(text), 0)
    return text + gap if align == LEFT else gap + text


def render_table(headers: Sequence[str], rows: Sequence[Sequence[str]], aligns: Sequence[str]) -> str:
    widths = [
        max([display_width(header)] + [display_width(row[i]) for row in rows])
        for i, header in enumerate(headers)
    ]

    def line(cells: Sequence[str]) -> str:
        return " | ".join(pad(cell, w, a) for cell, w, a in zip(cells, widths, aligns)).rstrip()

    separator = "-+-".join("-" * w for w in widths)
    return "\n".join([line(headers), separator, *(line(row) for row in rows)])


def won(amount: int) -> str:
    return f"{amount:,}원"


def transactions_table(transactions: Sequence[Transaction]) -> str:
    headers = ("ID", "날짜", "타입", "카테고리", "금액", "메모", "태그")
    rows = [
        (
            tx.id,
            tx.date,
            tx.type,
            truncate(tx.category, 14),
            f"{tx.amount:,}",
            truncate(tx.memo, 24),
            truncate(",".join(tx.tags), 20),
        )
        for tx in transactions
    ]
    return render_table(headers, rows, (LEFT, LEFT, LEFT, LEFT, RIGHT, LEFT, LEFT))


def transaction_line(tx: Transaction) -> str:
    memo = f" | {tx.memo}" if tx.memo else ""
    return f"{tx.id} | {tx.date} | {tx.type} | {tx.category} | {won(tx.amount)}{memo}"


def usage_bar(rate: float, width: int = 20) -> str:
    filled = min(int(rate / 100 * width), width)
    return "[" + "#" * filled + "." * (width - filled) + "]"


def budget_lines(summary: MonthlySummary) -> list[str]:
    if summary.budget is None:
        return [f"예산: 미설정 (설정: budget set --month {summary.month} --amount <금액>)"]
    rate = summary.usage_rate or 0.0
    lines = [f"예산: {won(summary.budget)} {usage_bar(rate)} 사용률 {rate:.1f}%"]
    if summary.over_budget:
        over = summary.expense - summary.budget
        lines.append(f"[경고] 예산을 {won(over)} 초과했습니다! 지출을 점검하세요.")
    elif rate >= WARNING_RATE:
        lines.append(f"[주의] 예산의 {WARNING_RATE:.0f}% 이상을 사용했습니다. (남은 예산 {won(summary.budget - summary.expense)})")
    else:
        lines.append(f"남은 예산: {won(summary.budget - summary.expense)}")
    return lines


def summary_report(summary: MonthlySummary, top: int) -> str:
    lines = [f"[{summary.month} 월별 요약]"]
    if not summary.has_data:
        lines.append(f"데이터 없음: {summary.month}에 기록된 거래가 없습니다.")
        if summary.budget is not None:
            lines.append(f"예산: {won(summary.budget)} (사용률 0.0%)")
        return "\n".join(lines)

    lines += [
        f"거래 수: {summary.count}건",
        f"총 수입: {won(summary.income)}",
        f"총 지출: {won(summary.expense)}",
        f"잔액: {won(summary.balance)}",
        *budget_lines(summary),
        "",
        f"지출 TOP {top}",
    ]
    if not summary.top_expenses:
        lines.append("  (지출 내역 없음)")
        return "\n".join(lines)

    name_width = max(display_width(name) for name, _ in summary.top_expenses)
    amount_width = max(display_width(won(amount)) for _, amount in summary.top_expenses)
    for rank, (name, amount) in enumerate(summary.top_expenses, start=1):
        share = amount / summary.expense * 100
        lines.append(
            f"{rank:>2}) {pad(name, name_width)}  {pad(won(amount), amount_width, RIGHT)}  ({share:.1f}%)"
        )
    return "\n".join(lines)


def categories_table(usage: Sequence[tuple[str, int]]) -> str:
    rows = [(name, f"{count:,}건") for name, count in usage]
    return render_table(("카테고리", "사용 거래"), rows, (LEFT, RIGHT))


def budgets_table(budgets: Sequence[Budget]) -> str:
    rows = [(b.month, won(b.amount)) for b in budgets]
    return render_table(("월", "예산"), rows, (LEFT, RIGHT))


def rules_table(rules: Sequence[RecurringRule]) -> str:
    rows = [
        (r.id, f"매월 {r.day}일", r.type, r.category, f"{r.amount:,}", truncate(r.memo, 24))
        for r in rules
    ]
    return render_table(
        ("ID", "주기", "타입", "카테고리", "금액", "메모"), rows, (LEFT, LEFT, LEFT, LEFT, RIGHT, LEFT)
    )


def print_error(message: str, hint: str | None = None) -> None:
    """오류는 표준 에러로: [오류] 원인 / [힌트] 해결 방법"""
    print(f"[오류] {message}", file=sys.stderr)
    if hint:
        print(f"[힌트] {hint}", file=sys.stderr)
