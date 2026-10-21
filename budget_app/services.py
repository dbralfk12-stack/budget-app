"""서비스 계층: 가계부의 규칙(비즈니스 로직)을 담는다.

- 저장소(storage)를 조합해 기능을 구현하고, 규칙 위반은 AppError로 알린다.
- 콘솔 입출력(input/print)은 하지 않는다. 그건 CLI 계층의 책임이다.
"""

from __future__ import annotations

import heapq
import json
import os
import zipfile
from collections import Counter, deque
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .decorators import log_call, timed
from .errors import ConflictError, NotFoundError, ValidationError
from .models import (
    TX_ID_PREFIX,
    ApplyResult,
    Budget,
    ImportResult,
    MonthlySummary,
    RecurringRule,
    Transaction,
    TransactionDraft,
    TransactionFilter,
    TransactionPatch,
    TxType,
    format_id,
    id_number,
)
from .storage import CSV_REQUIRED_COLUMNS, DATA_FILES, Storage, atomic_open, iter_csv_rows, write_csv
from .validators import (
    parse_amount,
    parse_category_name,
    parse_date,
    parse_memo,
    parse_tags,
    parse_type,
)

BACKUP_DIR_NAME = "backups"
MAX_REPORTED_ERRORS = 20


def draft_from_csv_row(row: Mapping[str, str]) -> TransactionDraft:
    """CSV 한 행을 검증해 TransactionDraft로 만든다."""
    for column in CSV_REQUIRED_COLUMNS:
        if not row.get(column):
            raise ValidationError(f"필수 값 '{column}'이(가) 비어 있습니다.")
    return TransactionDraft(
        type=parse_type(row["type"]),
        date=parse_date(row["date"]),
        amount=parse_amount(row["amount"]),
        category=parse_category_name(row["category"]),
        memo=parse_memo(row.get("memo", "")),
        tags=parse_tags(row.get("tags", "")),
    )


class LedgerService:
    """거래 추가/조회/검색/요약/수정/삭제/가져오기/내보내기."""

    def __init__(self, storage: Storage) -> None:
        self._transactions = storage.transactions
        self._categories = storage.categories
        self._budgets = storage.budgets

    def require_category(self, value: str) -> str:
        """등록된 카테고리인지 확인하고 정리된 이름을 돌려준다."""
        name = parse_category_name(value)
        names = self._categories.names()
        if name not in names:
            raise ValidationError(
                f"등록되지 않은 카테고리입니다: '{name}'",
                f"등록된 카테고리: {', '.join(names)} / 새로 등록하려면: category add --name {name}",
            )
        return name

    def get(self, tx_id: str) -> Transaction:
        tx = self._transactions.get(tx_id)
        if tx is None:
            raise NotFoundError(
                f"해당 id의 거래가 없습니다: {tx_id}", "'list' 또는 'search' 명령으로 id를 확인하세요."
            )
        return tx

    @log_call
    def add(self, draft: TransactionDraft) -> Transaction:
        self.require_category(draft.category)
        tx = draft.to_transaction(self._transactions.next_id())
        self._transactions.add(tx)
        return tx

    @timed
    def recent(self, limit: int) -> list[Transaction]:
        """최신순 상위 limit건. heapq.nlargest는 스트림을 끝까지 돌며 limit건만 메모리에 유지한다."""
        return heapq.nlargest(limit, self._transactions.stream(), key=Transaction.sort_key)

    @timed
    def search(self, condition: TransactionFilter, limit: int | None = None) -> list[Transaction]:
        """조건에 맞는 거래만 스트리밍으로 걸러낸 뒤 최신순 정렬한다."""
        matches = self._transactions.find(condition)
        if limit is None:
            return sorted(matches, key=Transaction.sort_key, reverse=True)
        return heapq.nlargest(limit, matches, key=Transaction.sort_key)

    @timed
    def monthly_summary(self, month: str, top: int) -> MonthlySummary:
        """한 달치를 스트리밍으로 한 번만 훑으며 합계를 누적한다."""
        summary = MonthlySummary(month=month)
        expense_by_category: Counter[str] = Counter()
        for tx in self._transactions.find(TransactionFilter.for_month(month)):
            summary.count += 1
            if tx.type == "income":
                summary.income += tx.amount
            else:
                summary.expense += tx.amount
                expense_by_category[tx.category] += tx.amount
        summary.top_expenses = expense_by_category.most_common(top)
        budget = self._budgets.get(month)
        summary.budget = budget.amount if budget else None
        return summary

    @log_call
    def update(self, tx_id: str, patch: TransactionPatch) -> tuple[Transaction, Transaction]:
        """수정 전/후 거래를 돌려준다."""
        if patch.is_empty():
            raise ValidationError(
                "수정할 항목이 없습니다.",
                "--date, --type, --category, --amount, --memo, --tags 중 하나 이상을 지정하세요.",
            )
        before = self.get(tx_id)
        if patch.category is not None:
            self.require_category(patch.category)
        after = patch.apply(before)
        self._transactions.replace(after)
        return before, after

    @log_call
    def delete(self, tx_id: str) -> Transaction:
        tx = self.get(tx_id)
        self._transactions.delete(tx_id)
        return tx

    @timed
    @log_call
    def import_csv(self, path: Path, *, create_categories: bool = False, dry_run: bool = False) -> ImportResult:
        """CSV를 한 행씩 검증하며 올바른 행만 transactions 파일 끝에 스트리밍으로 추가한다."""
        result = ImportResult()
        known = set(self._categories.names())
        next_number = id_number(self._transactions.next_id())

        def skip(line_no: int, reason: str) -> None:
            result.skipped += 1
            if len(result.errors) < MAX_REPORTED_ERRORS:
                result.errors.append(f"{line_no}행: {reason}")

        def valid_transactions() -> Iterator[Transaction]:
            nonlocal next_number
            for line_no, row in iter_csv_rows(path):
                try:
                    draft = draft_from_csv_row(row)
                except ValidationError as exc:
                    skip(line_no, exc.message)
                    continue
                if draft.category not in known:
                    if not create_categories:
                        skip(line_no, f"등록되지 않은 카테고리 '{draft.category}'")
                        continue
                    known.add(draft.category)
                    result.created_categories.append(draft.category)
                    if not dry_run:
                        self._categories.add(draft.category)
                result.imported += 1
                yield draft.to_transaction(format_id(TX_ID_PREFIX, next_number))
                next_number += 1

        if dry_run:
            deque(valid_transactions(), maxlen=0)   # 검증만 하고 저장하지 않음
        else:
            self._transactions.add_many(valid_transactions())
        return result

    @timed
    @log_call
    def export_csv(self, path: Path, condition: TransactionFilter, *, overwrite: bool = False) -> int:
        """조건에 맞는 거래를 저장 순서대로 스트리밍하며 CSV에 쓴다."""
        if path.exists() and not overwrite:
            raise ConflictError(f"이미 파일이 존재합니다: {path}", "덮어쓰려면 --force 옵션을 추가하세요.")
        return write_csv(path, self._transactions.find(condition))


class CategoryService:
    """카테고리 추가/조회/삭제. 사용 중인 카테고리는 대체 카테고리 없이 삭제할 수 없다."""

    def __init__(self, storage: Storage) -> None:
        self._categories = storage.categories
        self._transactions = storage.transactions
        self._rules = storage.recurring

    def names(self) -> list[str]:
        return self._categories.names()

    def usage(self) -> list[tuple[str, int]]:
        counts = Counter(tx.category for tx in self._transactions.stream())
        return [(name, counts[name]) for name in self._categories.names()]

    @log_call
    def add(self, value: str) -> str:
        name = parse_category_name(value)
        if self._categories.exists(name):
            raise ConflictError(f"이미 등록된 카테고리입니다: {name}", "'category list'로 목록을 확인하세요.")
        self._categories.add(name)
        return name

    @log_call
    def remove(self, value: str, replace_with: str | None = None) -> int:
        """카테고리를 삭제하고, 대체 카테고리로 옮긴 거래 수를 돌려준다."""
        name = parse_category_name(value)
        names = self._categories.names()
        if name not in names:
            raise NotFoundError(f"등록되지 않은 카테고리입니다: {name}", f"등록된 카테고리: {', '.join(names)}")
        if len(names) == 1:
            raise ValidationError("마지막 남은 카테고리는 삭제할 수 없습니다.", "다른 카테고리를 먼저 추가하세요.")

        tx_count = sum(1 for tx in self._transactions.stream() if tx.category == name)
        rule_count = sum(1 for rule in self._rules.all() if rule.category == name)
        if (tx_count or rule_count) and replace_with is None:
            raise ConflictError(
                f"'{name}' 카테고리를 사용하는 거래 {tx_count}건, 반복 규칙 {rule_count}건이 있어 삭제할 수 없습니다.",
                f"대체 카테고리를 지정하세요: category remove --name {name} --replace-with etc",
            )

        moved = 0
        if replace_with is not None:
            target = parse_category_name(replace_with)
            if target == name:
                raise ValidationError("대체 카테고리가 삭제할 카테고리와 같습니다.", "다른 카테고리를 지정하세요.")
            if target not in names:
                raise ValidationError(
                    f"대체 카테고리가 등록되어 있지 않습니다: {target}", f"등록된 카테고리: {', '.join(names)}"
                )
            # 거래를 먼저 옮기고 마지막에 카테고리를 지운다. 중간에 실패해도 '없는 카테고리를 쓰는 거래'가 생기지 않는다.
            moved = self._transactions.reassign_category(name, target) if tx_count else 0
            if rule_count:
                self._rules.reassign_category(name, target)
        self._categories.remove(name)
        return moved


class BudgetService:
    """월 예산 설정/조회."""

    def __init__(self, storage: Storage) -> None:
        self._budgets = storage.budgets

    @log_call
    def set(self, month: str, amount: int) -> Budget:
        budget = Budget(month=month, amount=amount)
        self._budgets.set(budget)
        return budget

    def get(self, month: str) -> Budget | None:
        return self._budgets.get(month)

    def all(self) -> list[Budget]:
        return self._budgets.all()


class RecurringService:
    """반복 거래 규칙 관리 및 특정 월 자동 생성."""

    def __init__(self, storage: Storage, ledger: LedgerService) -> None:
        self._rules = storage.recurring
        self._transactions = storage.transactions
        self._categories = storage.categories
        self._ledger = ledger

    def all(self) -> list[RecurringRule]:
        return self._rules.all()

    @log_call
    def add(
        self, *, day: int, type: TxType, category: str, amount: int, memo: str = "", tags: list[str] | None = None
    ) -> RecurringRule:
        rule = RecurringRule(
            id=self._rules.next_id(),
            day=day,
            type=type,
            category=self._ledger.require_category(category),
            amount=amount,
            memo=memo,
            tags=tags or [],
        )
        self._rules.add(rule)
        return rule

    @log_call
    def remove(self, rule_id: str) -> RecurringRule:
        rule = next((r for r in self._rules.all() if r.id == rule_id), None)
        if rule is None:
            raise NotFoundError(f"해당 id의 반복 규칙이 없습니다: {rule_id}", "'recurring list'로 id를 확인하세요.")
        self._rules.remove(rule_id)
        return rule

    @timed
    @log_call
    def apply(self, month: str) -> ApplyResult:
        """규칙마다 해당 월 거래를 1건씩 만든다. 이미 만든 규칙은 건너뛰므로 여러 번 실행해도 안전하다."""
        result = ApplyResult()
        rules = self._rules.all()
        if not rules:
            return result
        already = {
            tx.recurring_id
            for tx in self._transactions.find(TransactionFilter.for_month(month))
            if tx.recurring_id
        }
        categories = set(self._categories.names())
        next_number = id_number(self._transactions.next_id())
        for rule in rules:
            if rule.id in already:
                result.skipped.append(f"{rule.id}: {month}에 이미 생성됨")
                continue
            if rule.category not in categories:
                result.skipped.append(f"{rule.id}: 카테고리 '{rule.category}'가 등록되어 있지 않음")
                continue
            tx = rule.to_draft(month).to_transaction(format_id(TX_ID_PREFIX, next_number), recurring_id=rule.id)
            next_number += 1
            result.created.append(tx)
        self._transactions.add_many(result.created)
        return result


class BackupService:
    """데이터 파일 전체를 타임스탬프가 붙은 zip으로 백업/복원."""

    def __init__(self, storage: Storage) -> None:
        self._data_dir = storage.data_dir
        self.backup_dir = storage.data_dir / BACKUP_DIR_NAME

    @log_call
    def create(self) -> Path:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.backup_dir / f"backup-{stamp}.zip"
        suffix = 1
        while path.exists():
            path = self.backup_dir / f"backup-{stamp}-{suffix}.zip"
            suffix += 1
        tmp_path = path.with_name(path.name + ".tmp")
        try:
            with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name in DATA_FILES:
                    source = self._data_dir / name
                    if source.exists():
                        archive.write(source, arcname=name)
            os.replace(tmp_path, path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        return path

    def list(self) -> list[Path]:
        if not self.backup_dir.exists():
            return []
        return sorted(self.backup_dir.glob("backup-*.zip"), reverse=True)

    @log_call
    def restore(self, source: Path) -> Path:
        """백업 내용을 검증한 뒤 현재 데이터를 먼저 백업하고 복원한다. 안전 백업 경로를 돌려준다."""
        if not source.is_file() and (self.backup_dir / source.name).is_file():
            source = self.backup_dir / source.name
        if not source.is_file():
            raise NotFoundError(f"백업 파일을 찾을 수 없습니다: {source}", "'backup list'로 백업 파일 이름을 확인하세요.")
        try:
            with zipfile.ZipFile(source) as archive:
                members = [name for name in archive.namelist() if name in DATA_FILES]
                contents = {name: archive.read(name).decode("utf-8") for name in members}
        except zipfile.BadZipFile:
            raise ValidationError(f"백업 파일 형식이 올바르지 않습니다: {source}", "backup 명령으로 만든 zip 파일을 지정하세요.") from None
        if not contents:
            raise ValidationError(f"백업 파일에 가계부 데이터가 없습니다: {source}", "backup 명령으로 만든 zip 파일을 지정하세요.")
        for name, text in contents.items():
            _validate_jsonl(name, text)

        safety_backup = self.create()
        for name, text in contents.items():
            with atomic_open(self._data_dir / name) as file:
                file.write(text)
        return safety_backup


def _validate_jsonl(name: str, text: str) -> None:
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            valid = isinstance(json.loads(line), dict)
        except json.JSONDecodeError:
            valid = False
        if not valid:
            raise ValidationError(
                f"백업 파일 안의 {name} {line_no}번째 줄이 손상되어 복원하지 않았습니다.",
                "다른 백업 파일을 지정하세요. 현재 데이터는 변경되지 않았습니다.",
            )


@dataclass(slots=True)
class BudgetApp:
    """CLI가 사용하는 서비스 묶음."""

    storage: Storage
    ledger: LedgerService
    categories: CategoryService
    budgets: BudgetService
    recurring: RecurringService
    backups: BackupService

    @classmethod
    def open(cls, data_dir: Path) -> tuple[BudgetApp, list[str]]:
        """데이터 폴더를 초기화하고 앱과 첫 실행 안내 문구를 돌려준다."""
        storage = Storage(data_dir)
        notices = storage.initialize()
        ledger = LedgerService(storage)
        app = cls(
            storage=storage,
            ledger=ledger,
            categories=CategoryService(storage),
            budgets=BudgetService(storage),
            recurring=RecurringService(storage, ledger),
            backups=BackupService(storage),
        )
        return app, notices
