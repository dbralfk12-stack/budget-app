"""CLI 계층: 명령행 인자 해석, 대화형 입력, 결과 출력만 담당한다.

규칙/계산은 서비스 계층(services.py)에, 파일 입출력은 저장소 계층(storage.py)에 맡긴다.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import NoReturn, TypeVar

from . import __version__
from . import formatter as fmt
from . import validators as v
from .decorators import handle_errors
from .errors import EXIT_ERROR, EXIT_OK, EXIT_USAGE, ValidationError
from .models import RULE_ID_PREFIX, TX_ID_PREFIX, TransactionDraft, TransactionFilter, TransactionPatch, month_bounds
from .services import BudgetApp

PROG = "python -m budget_app"
DEFAULT_DATA_DIR = "data"
DATA_DIR_ENV = "BUDGET_APP_DATA_DIR"
LOG_FILE = "app.log"
DEFAULT_LIST_LIMIT = 10
DEFAULT_TOP = 5

Handler = Callable[[BudgetApp, argparse.Namespace], int]
T = TypeVar("T")

logger = logging.getLogger("budget_app")


# ---------------------------------------------------------------- 입력 도우미

def ask(label: str, parse: Callable[[str], T]) -> T:
    """올바른 값이 들어올 때까지 다시 묻는다(재입력 요구)."""
    while True:
        try:
            return parse(input(label))
        except ValidationError as exc:
            fmt.print_error(exc.message, exc.hint)


def this_month() -> str:
    return date.today().strftime("%Y-%m")


def build_filter(args: argparse.Namespace, *, require_period: bool = False) -> TransactionFilter:
    """search/export 공통 조건 옵션을 TransactionFilter로 바꾼다."""
    if args.month and (args.date_from or args.date_to):
        raise ValidationError("--month 와 --from/--to 는 함께 쓸 수 없습니다.", "둘 중 하나만 지정하세요.")
    if args.month:
        date_from, date_to = month_bounds(v.parse_month(args.month))
    else:
        date_from, date_to = v.parse_period(args.date_from, args.date_to)
    if require_period and not (date_from or date_to):
        raise ValidationError(
            "기간 조건이 필요합니다.",
            "예: --month 2024-01  또는  --from 2024-01-01 --to 2024-03-31",
        )
    return TransactionFilter(
        date_from=date_from,
        date_to=date_to,
        category=v.parse_category_name(args.category) if args.category else None,
        type=v.parse_type(args.type) if args.type else None,
        keyword=args.q.strip() if args.q else None,
        tag=args.tag.strip() if args.tag else None,
    )


# ---------------------------------------------------------------- 거래 명령

def cmd_add(app: BudgetApp, args: argparse.Namespace) -> int:
    today = date.today().isoformat()
    print(f"등록된 카테고리: {', '.join(app.categories.names())}")
    draft = TransactionDraft(
        date=ask(f"날짜(YYYY-MM-DD, 엔터={today}): ", lambda raw: v.parse_date(raw.strip() or today)),
        type=ask("타입(income/expense): ", v.parse_type),
        category=ask("카테고리: ", app.ledger.require_category),
        amount=ask("금액(양수): ", v.parse_amount),
        memo=ask("메모(선택): ", v.parse_memo),
        tags=ask("태그(쉼표로 구분, 없으면 엔터): ", v.parse_tags),
    )
    tx = app.ledger.add(draft)
    print(f"[저장 완료] id={tx.id}")
    return EXIT_OK


def cmd_list(app: BudgetApp, args: argparse.Namespace) -> int:
    limit = v.parse_positive_int(args.limit, "--limit")
    transactions = app.ledger.recent(limit)
    if not transactions:
        print("거래 내역이 없습니다. 'add' 명령으로 첫 거래를 추가해 보세요.")
        return EXIT_OK
    print(fmt.transactions_table(transactions))
    print(f"\n최신순 {len(transactions)}건 (--limit {limit})")
    return EXIT_OK


def cmd_search(app: BudgetApp, args: argparse.Namespace) -> int:
    condition = build_filter(args)
    limit = v.parse_positive_int(args.limit, "--limit") if args.limit is not None else None
    results = app.ledger.search(condition, limit)
    if not results:
        print("조건에 맞는 거래가 없습니다.")
        return EXIT_OK
    print(fmt.transactions_table(results))
    print(f"\n검색 결과 {len(results)}건 (최신순)")
    return EXIT_OK


def cmd_summary(app: BudgetApp, args: argparse.Namespace) -> int:
    month = v.parse_month(args.month) if args.month else this_month()
    top = v.parse_positive_int(args.top, "--top")
    print(fmt.summary_report(app.ledger.monthly_summary(month, top), top))
    return EXIT_OK


def cmd_update(app: BudgetApp, args: argparse.Namespace) -> int:
    tx_id = v.parse_id(args.id, TX_ID_PREFIX)
    patch = TransactionPatch(
        date=v.parse_date(args.date) if args.date is not None else None,
        type=v.parse_type(args.type) if args.type is not None else None,
        category=v.parse_category_name(args.category) if args.category is not None else None,
        amount=v.parse_amount(args.amount) if args.amount is not None else None,
        memo=v.parse_memo(args.memo) if args.memo is not None else None,
        tags=v.parse_tags(args.tags) if args.tags is not None else None,
    )
    before, after = app.ledger.update(tx_id, patch)
    print(f"[수정 완료] id={after.id}")
    print(f"  수정 전: {fmt.transaction_line(before)}")
    print(f"  수정 후: {fmt.transaction_line(after)}")
    return EXIT_OK


def cmd_delete(app: BudgetApp, args: argparse.Namespace) -> int:
    tx = app.ledger.delete(v.parse_id(args.id, TX_ID_PREFIX))
    print(f"[삭제 완료] {fmt.transaction_line(tx)}")
    return EXIT_OK


def cmd_import(app: BudgetApp, args: argparse.Namespace) -> int:
    result = app.ledger.import_csv(
        Path(args.source), create_categories=args.create_categories, dry_run=args.dry_run
    )
    label = "[검증 완료 - dry-run, 저장하지 않음]" if args.dry_run else "[완료]"
    print(f"{label} imported={result.imported}, skipped={result.skipped}")
    if result.created_categories:
        print(f"새로 등록한 카테고리: {', '.join(result.created_categories)}")
    if result.errors:
        print("건너뛴 행:")
        for error in result.errors:
            print(f"  - {error}")
        hidden = result.skipped - len(result.errors)
        if hidden > 0:
            print(f"  ... 외 {hidden}건")
    if result.imported == 0 and result.skipped > 0:
        fmt.print_error("가져온 거래가 없습니다.", "위의 '건너뛴 행' 사유를 확인해 CSV를 고친 뒤 다시 실행하세요.")
        return EXIT_ERROR
    return EXIT_OK


def cmd_export(app: BudgetApp, args: argparse.Namespace) -> int:
    condition = build_filter(args, require_period=True)
    out = Path(args.out)
    count = app.ledger.export_csv(out, condition, overwrite=args.force)
    print(f"[완료] {out} ({count} records)")
    return EXIT_OK


# ---------------------------------------------------------------- 예산 / 카테고리

def cmd_budget_set(app: BudgetApp, args: argparse.Namespace) -> int:
    budget = app.budgets.set(v.parse_month(args.month), v.parse_amount(args.amount))
    print(f"[저장 완료] {budget.month} 예산 {fmt.won(budget.amount)}")
    return EXIT_OK


def cmd_budget_get(app: BudgetApp, args: argparse.Namespace) -> int:
    month = v.parse_month(args.month) if args.month else this_month()
    summary = app.ledger.monthly_summary(month, top=0)
    print(f"[{month} 예산]")
    print(f"총 지출: {fmt.won(summary.expense)}")
    for line in fmt.budget_lines(summary):
        print(line)
    return EXIT_OK


def cmd_budget_list(app: BudgetApp, args: argparse.Namespace) -> int:
    budgets = app.budgets.all()
    if not budgets:
        print("설정된 예산이 없습니다. (설정: budget set --month YYYY-MM --amount <금액>)")
        return EXIT_OK
    print(fmt.budgets_table(budgets))
    return EXIT_OK


def cmd_category_add(app: BudgetApp, args: argparse.Namespace) -> int:
    name = args.name if args.name is not None else ask("카테고리명: ", v.parse_category_name)
    print(f"[저장 완료] category={app.categories.add(name)}")
    return EXIT_OK


def cmd_category_list(app: BudgetApp, args: argparse.Namespace) -> int:
    print(fmt.categories_table(app.categories.usage()))
    return EXIT_OK


def cmd_category_remove(app: BudgetApp, args: argparse.Namespace) -> int:
    moved = app.categories.remove(args.name, args.replace_with)
    message = f"[삭제 완료] category={args.name.strip()}"
    if args.replace_with:
        message += f" (거래 {moved}건을 '{args.replace_with.strip()}'(으)로 이동)"
    print(message)
    return EXIT_OK


# ---------------------------------------------------------------- 보너스: 백업 / 반복 거래

def cmd_backup_create(app: BudgetApp, args: argparse.Namespace) -> int:
    print(f"[백업 완료] {app.backups.create()}")
    return EXIT_OK


def cmd_backup_list(app: BudgetApp, args: argparse.Namespace) -> int:
    backups = app.backups.list()
    if not backups:
        print("백업 파일이 없습니다. 'backup' 명령으로 만들 수 있습니다.")
        return EXIT_OK
    rows = [(path.name, f"{path.stat().st_size:,} bytes") for path in backups]
    print(fmt.render_table(("백업 파일", "크기"), rows, (fmt.LEFT, fmt.RIGHT)))
    print(f"\n위치: {app.backups.backup_dir}")
    return EXIT_OK


def cmd_backup_restore(app: BudgetApp, args: argparse.Namespace) -> int:
    safety = app.backups.restore(Path(args.file))
    print(f"[복원 완료] {args.file}")
    print(f"복원 직전 데이터는 {safety} 에 백업해 두었습니다.")
    return EXIT_OK


def cmd_recurring_add(app: BudgetApp, args: argparse.Namespace) -> int:
    rule = app.recurring.add(
        day=v.parse_day(args.day),
        type=v.parse_type(args.type),
        category=args.category,
        amount=v.parse_amount(args.amount),
        memo=v.parse_memo(args.memo or ""),
        tags=v.parse_tags(args.tags or ""),
    )
    print(f"[저장 완료] id={rule.id} (매월 {rule.day}일 {rule.type} {rule.category} {fmt.won(rule.amount)})")
    return EXIT_OK


def cmd_recurring_list(app: BudgetApp, args: argparse.Namespace) -> int:
    rules = app.recurring.all()
    if not rules:
        print("등록된 반복 규칙이 없습니다. (등록: recurring add --day 25 --type income --category salary --amount 3000000)")
        return EXIT_OK
    print(fmt.rules_table(rules))
    return EXIT_OK


def cmd_recurring_remove(app: BudgetApp, args: argparse.Namespace) -> int:
    rule = app.recurring.remove(v.parse_id(args.id, RULE_ID_PREFIX))
    print(f"[삭제 완료] {rule.id} (매월 {rule.day}일 {rule.category} {fmt.won(rule.amount)})")
    return EXIT_OK


def cmd_recurring_apply(app: BudgetApp, args: argparse.Namespace) -> int:
    month = v.parse_month(args.month)
    result = app.recurring.apply(month)
    print(f"[완료] {month} 반복 거래 created={len(result.created)}, skipped={len(result.skipped)}")
    for tx in result.created:
        print(f"  + {fmt.transaction_line(tx)}")
    for reason in result.skipped:
        print(f"  - 건너뜀 {reason}")
    return EXIT_OK


# ---------------------------------------------------------------- 파서

class KoreanArgumentParser(argparse.ArgumentParser):
    """사용법 오류도 다른 오류와 같은 '[오류] / [힌트]' 형식으로 출력한다."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        fmt.print_error(f"명령 인자가 올바르지 않습니다: {message}", f"'{self.prog} --help'로 사용법을 확인하세요.")
        raise SystemExit(EXIT_USAGE)


def _add_filter_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--month", metavar="YYYY-MM", help="해당 월 전체 (--from/--to와 함께 쓸 수 없음)")
    parser.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD", help="시작일(포함)")
    parser.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD", help="종료일(포함)")
    parser.add_argument("--category", help="카테고리 일치")
    parser.add_argument("--type", help="income 또는 expense")
    parser.add_argument("--q", metavar="KEYWORD", help="메모에 포함된 키워드(대소문자 무시)")
    parser.add_argument("--tag", help="태그 일치")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--data-dir",
        default=argparse.SUPPRESS,
        metavar="DIR",
        help=f"데이터 폴더 (기본: ./{DEFAULT_DATA_DIR}, 환경변수 {DATA_DIR_ENV}로도 지정 가능)",
    )
    common.add_argument(
        "--verbose", action="store_true", default=argparse.SUPPRESS, help="실행 로그와 실행 시간을 화면에도 출력"
    )

    parser = KoreanArgumentParser(
        prog=PROG,
        description="나만의 용돈 기입장 - 파일(JSONL) 기반 콘솔 가계부",
        parents=[common],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="각 명령의 자세한 사용법: python -m budget_app <command> --help",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="<command>", required=True, title="명령")

    def command(
        group: argparse._SubParsersAction[argparse.ArgumentParser],
        name: str,
        summary: str,
        handler: Handler | None,
        examples: str | None = None,
    ) -> argparse.ArgumentParser:
        sub = group.add_parser(
            name,
            help=summary,
            description=summary,
            parents=[common],
            epilog=f"예시:\n{examples}" if examples else None,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        if handler is not None:
            sub.set_defaults(handler=handler)
        return sub

    command(commands, "add", "거래 추가 (대화형 입력)", cmd_add, "  python -m budget_app add")

    p = command(commands, "list", "거래 목록 (최신순)", cmd_list, "  python -m budget_app list --limit 5")
    p.add_argument("--limit", type=int, default=DEFAULT_LIST_LIMIT, help=f"출력 건수 (기본 {DEFAULT_LIST_LIMIT})")

    p = command(
        commands,
        "search",
        "조건 검색 (최신순)",
        cmd_search,
        "  python -m budget_app search --from 2024-01-01 --to 2024-01-31 --type expense\n"
        "  python -m budget_app search --category food --q 점심 --tag meal",
    )
    _add_filter_options(p)
    p.add_argument("--limit", type=int, help="최대 출력 건수 (기본: 전체)")

    p = command(
        commands, "summary", "월별 요약 + 예산 사용률", cmd_summary,
        "  python -m budget_app summary --month 2024-01 --top 3",
    )
    p.add_argument("--month", metavar="YYYY-MM", help="요약할 월 (기본: 이번 달)")
    p.add_argument("--top", type=int, default=DEFAULT_TOP, help=f"카테고리별 지출 TOP N (기본 {DEFAULT_TOP})")

    p = command(
        commands,
        "update",
        "거래 수정 (옵션 방식: 지정한 필드만 바뀜)",
        cmd_update,
        "  python -m budget_app update --id TX-000012 --amount 12000 --memo 저녁\n"
        "  python -m budget_app update --id 12 --tags \"\"      # 태그 비우기",
    )
    p.add_argument("--id", required=True, help="수정할 거래 id (예: TX-000012 또는 12)")
    p.add_argument("--date", metavar="YYYY-MM-DD")
    p.add_argument("--type", help="income 또는 expense")
    p.add_argument("--category")
    p.add_argument("--amount", help="양수 정수")
    p.add_argument("--memo", help='빈 문자열("")이면 메모 삭제')
    p.add_argument("--tags", help='쉼표 구분, 빈 문자열("")이면 태그 삭제')

    p = command(commands, "delete", "거래 삭제", cmd_delete, "  python -m budget_app delete --id TX-000012")
    p.add_argument("--id", required=True, help="삭제할 거래 id")

    p = command(
        commands,
        "import",
        "CSV에서 거래 일괄 등록",
        cmd_import,
        "  python -m budget_app import --from samples/import_sample.csv\n"
        "  python -m budget_app import --from data.csv --dry-run --create-categories",
    )
    p.add_argument("--from", dest="source", required=True, metavar="CSV", help="가져올 CSV 파일 (UTF-8, 헤더 포함)")
    p.add_argument("--create-categories", action="store_true", help="등록되지 않은 카테고리를 자동 등록")
    p.add_argument("--dry-run", action="store_true", help="저장하지 않고 검증 결과만 출력")

    p = command(
        commands,
        "export",
        "조건에 맞는 거래를 CSV로 저장 (--month 또는 --from/--to 필수)",
        cmd_export,
        "  python -m budget_app export --out export.csv --month 2024-01\n"
        "  python -m budget_app export --out q1.csv --from 2024-01-01 --to 2024-03-31 --type expense",
    )
    p.add_argument("--out", required=True, metavar="CSV", help="저장할 CSV 파일 경로")
    _add_filter_options(p)
    p.add_argument("--force", action="store_true", help="같은 이름의 파일이 있으면 덮어쓰기")

    budget = command(commands, "budget", "월 예산 설정/조회", None)
    budget_cmds = budget.add_subparsers(dest="action", metavar="<action>", required=True, title="동작")
    p = command(budget_cmds, "set", "월 예산 저장", cmd_budget_set,
                "  python -m budget_app budget set --month 2024-01 --amount 500000")
    p.add_argument("--month", required=True, metavar="YYYY-MM")
    p.add_argument("--amount", required=True, help="양수 정수")
    p = command(budget_cmds, "get", "월 예산과 사용률 조회", cmd_budget_get,
                "  python -m budget_app budget get --month 2024-01")
    p.add_argument("--month", metavar="YYYY-MM", help="기본: 이번 달")
    command(budget_cmds, "list", "설정된 예산 전체 목록", cmd_budget_list)

    category = command(commands, "category", "카테고리 관리", None)
    category_cmds = category.add_subparsers(dest="action", metavar="<action>", required=True, title="동작")
    p = command(category_cmds, "add", "카테고리 추가 (--name 없으면 대화형)", cmd_category_add,
                "  python -m budget_app category add\n  python -m budget_app category add --name 용돈")
    p.add_argument("--name", help="추가할 카테고리 이름")
    command(category_cmds, "list", "카테고리 목록과 사용 거래 수", cmd_category_list)
    p = command(
        category_cmds,
        "remove",
        "카테고리 삭제 (사용 중이면 --replace-with 필요)",
        cmd_category_remove,
        "  python -m budget_app category remove --name shopping\n"
        "  python -m budget_app category remove --name shopping --replace-with etc",
    )
    p.add_argument("--name", required=True, help="삭제할 카테고리 이름")
    p.add_argument("--replace-with", metavar="CATEGORY", help="사용 중인 거래를 옮길 대체 카테고리")

    backup = command(
        commands,
        "backup",
        "데이터 백업 (하위 명령 없이 실행하면 백업 생성)",
        cmd_backup_create,
        "  python -m budget_app backup\n"
        "  python -m budget_app backup list\n"
        "  python -m budget_app backup restore --file backup-20240115-093000.zip",
    )
    backup_cmds = backup.add_subparsers(dest="action", metavar="<action>", title="동작")
    command(backup_cmds, "create", "타임스탬프가 포함된 zip 백업 생성", cmd_backup_create)
    command(backup_cmds, "list", "백업 파일 목록", cmd_backup_list)
    p = command(backup_cmds, "restore", "백업에서 복원 (복원 직전 상태도 자동 백업)", cmd_backup_restore)
    p.add_argument("--file", required=True, help="백업 zip 경로 또는 backups 폴더 안의 파일 이름")

    recurring = command(commands, "recurring", "반복 거래(월급/월세 등) 관리", None)
    recurring_cmds = recurring.add_subparsers(dest="action", metavar="<action>", required=True, title="동작")
    p = command(
        recurring_cmds,
        "add",
        "반복 규칙 등록",
        cmd_recurring_add,
        "  python -m budget_app recurring add --day 25 --type income --category salary --amount 3000000 --memo 월급",
    )
    p.add_argument("--day", required=True, help="매월 생성할 날짜(1~31, 없는 날짜면 말일)")
    p.add_argument("--type", required=True, help="income 또는 expense")
    p.add_argument("--category", required=True)
    p.add_argument("--amount", required=True, help="양수 정수")
    p.add_argument("--memo")
    p.add_argument("--tags", help="쉼표 구분")
    command(recurring_cmds, "list", "반복 규칙 목록", cmd_recurring_list)
    p = command(recurring_cmds, "remove", "반복 규칙 삭제", cmd_recurring_remove,
                "  python -m budget_app recurring remove --id RC-000001")
    p.add_argument("--id", required=True)
    p = command(recurring_cmds, "apply", "해당 월의 반복 거래 생성 (중복 생성 안 함)", cmd_recurring_apply,
                "  python -m budget_app recurring apply --month 2024-02")
    p.add_argument("--month", required=True, metavar="YYYY-MM")

    return parser


# ---------------------------------------------------------------- 실행

def resolve_data_dir(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "data_dir", None) or os.environ.get(DATA_DIR_ENV) or DEFAULT_DATA_DIR)


def configure_logging(data_dir: Path, verbose: bool) -> None:
    close_logging()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    file_handler = logging.FileHandler(data_dir / LOG_FILE, encoding="utf-8", delay=True)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(file_handler)
    if verbose:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(logging.Formatter("[로그] %(message)s"))
        logger.addHandler(stream_handler)


def close_logging() -> None:
    for handler in list(logger.handlers):
        if not isinstance(handler, logging.NullHandler):
            logger.removeHandler(handler)
            handler.close()


@handle_errors
def run(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args)
    app, notices = BudgetApp.open(data_dir)
    configure_logging(data_dir, getattr(args, "verbose", False))
    logger.info("명령 실행: %s %s", args.command, getattr(args, "action", "") or "")
    for notice in notices:
        print(f"[안내] {notice}")
    handler: Handler = args.handler
    return handler(app, args)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    finally:
        close_logging()
