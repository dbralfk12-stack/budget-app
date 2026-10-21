"""표준 라이브러리 unittest로 작성한 통합 테스트.

실행: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from budget_app.cli import main
from budget_app.formatter import display_width, pad
from budget_app.storage import CATEGORIES_FILE, DEFAULT_CATEGORIES, TRANSACTIONS_FILE


class CliTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.data_dir = self.root / "data"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_cli(self, *argv: str, inputs: list[str] | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        answers = iter(inputs or [])

        def fake_input(prompt: str = "") -> str:
            out.write(prompt)
            try:
                return next(answers)
            except StopIteration:
                raise EOFError from None

        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch("builtins.input", fake_input):
            try:
                code = main([*argv, "--data-dir", str(self.data_dir)])
            except SystemExit as exc:   # argparse --help / 사용법 오류
                code = int(exc.code or 0)
        return code, out.getvalue(), err.getvalue()

    def add(self, date: str, tx_type: str, category: str, amount: str, memo: str = "", tags: str = "") -> str:
        code, out, err = self.run_cli("add", inputs=[date, tx_type, category, amount, memo, tags])
        self.assertEqual(code, 0, err)
        return out.strip().splitlines()[-1].split("id=")[1]

    def read_jsonl(self, name: str) -> list[dict]:
        lines = (self.data_dir / name).read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]


class InitAndAddTest(CliTestCase):
    def test_first_run_creates_files_and_default_categories(self) -> None:
        code, out, _ = self.run_cli("category", "list")
        self.assertEqual(code, 0)
        self.assertIn("[안내]", out)
        for name in ("transactions.jsonl", "categories.jsonl", "budgets.jsonl", "recurring.jsonl"):
            self.assertTrue((self.data_dir / name).exists(), name)
        self.assertEqual([r["name"] for r in self.read_jsonl(CATEGORIES_FILE)], list(DEFAULT_CATEGORIES))

    def test_add_saves_and_prints_id(self) -> None:
        tx_id = self.add("2024-01-15", "expense", "food", "15,000", "점심", "meal, lunch")
        self.assertEqual(tx_id, "TX-000001")
        [record] = self.read_jsonl(TRANSACTIONS_FILE)
        self.assertEqual(record["amount"], 15000)
        self.assertEqual(record["tags"], ["meal", "lunch"])

    def test_add_reprompts_on_invalid_input(self) -> None:
        code, out, err = self.run_cli(
            "add",
            inputs=["2024-13-40", "2024-01-15", "spend", "expense", "nope", "food", "-5", "0", "abc", "1000", "", ""],
        )
        self.assertEqual(code, 0, err)
        self.assertIn("날짜 형식이 올바르지 않습니다", err)
        self.assertIn("허용되지 않은 타입", err)
        self.assertIn("등록되지 않은 카테고리", err)
        self.assertIn("양수", err)
        self.assertIn("[힌트]", err)
        self.assertIn("[저장 완료] id=TX-000001", out)

    def test_add_cancelled_by_eof_saves_nothing(self) -> None:
        code, _, err = self.run_cli("add", inputs=["2024-01-15"])
        self.assertEqual(code, 130)
        self.assertIn("취소", err)
        self.assertEqual(self.read_jsonl(TRANSACTIONS_FILE), [])


class QueryTest(CliTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.add("2024-01-15", "expense", "food", "15000", "점심", "meal")
        self.add("2024-01-14", "income", "salary", "3000000", "월급")
        self.add("2024-01-12", "expense", "transport", "20000")
        self.add("2024-02-01", "expense", "rent", "500000", "2월 월세", "house")

    def test_list_is_latest_first_and_limited(self) -> None:
        code, out, _ = self.run_cli("list", "--limit", "2")
        self.assertEqual(code, 0)
        self.assertLess(out.index("TX-000004"), out.index("TX-000001"))
        self.assertNotIn("TX-000002", out)

    def test_search_conditions(self) -> None:
        _, out, _ = self.run_cli("search", "--from", "2024-01-01", "--to", "2024-01-31", "--type", "expense")
        self.assertIn("TX-000001", out)
        self.assertIn("TX-000003", out)
        self.assertNotIn("TX-000002", out)
        self.assertNotIn("TX-000004", out)
        _, out, _ = self.run_cli("search", "--q", "점심", "--tag", "MEAL")
        self.assertIn("검색 결과 1건", out)

    def test_search_rejects_reversed_period(self) -> None:
        code, _, err = self.run_cli("search", "--from", "2024-02-01", "--to", "2024-01-01")
        self.assertNotEqual(code, 0)
        self.assertIn("시작일이 종료일보다 늦습니다", err)

    def test_summary_with_budget_warning(self) -> None:
        self.run_cli("budget", "set", "--month", "2024-01", "--amount", "30000")
        code, out, _ = self.run_cli("summary", "--month", "2024-01", "--top", "1")
        self.assertEqual(code, 0)
        self.assertIn("총 수입: 3,000,000원", out)
        self.assertIn("총 지출: 35,000원", out)
        self.assertIn("잔액: 2,965,000원", out)
        self.assertIn("[경고]", out)
        self.assertIn("1) transport", out)
        self.assertNotIn("2) food", out)

    def test_summary_without_data(self) -> None:
        code, out, _ = self.run_cli("summary", "--month", "2023-12")
        self.assertEqual(code, 0)
        self.assertIn("데이터 없음", out)


class UpdateDeleteTest(CliTestCase):
    def test_update_changes_only_given_fields(self) -> None:
        tx_id = self.add("2024-01-15", "expense", "food", "15000", "점심", "meal")
        code, out, _ = self.run_cli("update", "--id", tx_id, "--amount", "12000", "--tags", "")
        self.assertEqual(code, 0)
        self.assertIn("[수정 완료]", out)
        [record] = self.read_jsonl(TRANSACTIONS_FILE)
        self.assertEqual((record["amount"], record["memo"], record["tags"]), (12000, "점심", []))

    def test_update_and_delete_missing_id(self) -> None:
        code, _, err = self.run_cli("update", "--id", "99", "--amount", "1")
        self.assertEqual(code, 3)
        self.assertIn("해당 id의 거래가 없습니다: TX-000099", err)
        code, _, err = self.run_cli("delete", "--id", "TX-000099")
        self.assertEqual(code, 3)

    def test_delete_rewrites_file_without_leftover_temp(self) -> None:
        self.add("2024-01-15", "expense", "food", "15000")
        self.add("2024-01-16", "expense", "food", "16000")
        code, out, _ = self.run_cli("delete", "--id", "TX-000001")
        self.assertEqual(code, 0)
        self.assertEqual([r["id"] for r in self.read_jsonl(TRANSACTIONS_FILE)], ["TX-000002"])
        self.assertEqual(list(self.data_dir.glob("*.tmp")), [])


class CategoryTest(CliTestCase):
    def test_remove_in_use_requires_replacement(self) -> None:
        self.add("2024-01-15", "expense", "shopping", "5000")
        code, _, err = self.run_cli("category", "remove", "--name", "shopping")
        self.assertEqual(code, 1)
        self.assertIn("--replace-with", err)
        code, out, _ = self.run_cli("category", "remove", "--name", "shopping", "--replace-with", "etc")
        self.assertEqual(code, 0)
        self.assertIn("1건", out)
        self.assertEqual(self.read_jsonl(TRANSACTIONS_FILE)[0]["category"], "etc")

    def test_add_interactive_and_duplicate(self) -> None:
        code, out, _ = self.run_cli("category", "add", inputs=["용돈"])
        self.assertEqual(code, 0)
        self.assertIn("category=용돈", out)
        code, _, err = self.run_cli("category", "add", "--name", "용돈")
        self.assertEqual(code, 1)
        self.assertIn("이미 등록된", err)


class ImportExportTest(CliTestCase):
    def test_import_then_export_roundtrip(self) -> None:
        source = self.root / "in.csv"
        source.write_text(
            "date,type,category,amount,memo,tags\n"
            "2024-01-05,income,salary,3000000,월급,\n"
            '2024-01-10,expense,food,12000,"점심, 회사","meal,work"\n'
            "2024-01-11,expense,unknown,5000,,\n"
            "2024-02-30,expense,food,5000,,\n",
            encoding="utf-8-sig",
        )
        code, out, _ = self.run_cli("import", "--from", str(source))
        self.assertEqual(code, 0)
        self.assertIn("imported=2, skipped=2", out)

        target = self.root / "out.csv"
        code, out, _ = self.run_cli("export", "--out", str(target), "--month", "2024-01")
        self.assertEqual(code, 0)
        self.assertIn("(2 records)", out)
        lines = target.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "date,type,category,amount,memo,tags")
        self.assertEqual(lines[2], '2024-01-10,expense,food,12000,"점심, 회사","meal,work"')

        code, _, err = self.run_cli("export", "--out", str(target), "--month", "2024-01")
        self.assertEqual(code, 1)
        self.assertIn("--force", err)

    def test_export_requires_period(self) -> None:
        code, _, err = self.run_cli("export", "--out", str(self.root / "x.csv"))
        self.assertEqual(code, 1)
        self.assertIn("기간 조건", err)

    def test_import_missing_header(self) -> None:
        source = self.root / "bad.csv"
        source.write_text("when,amount\n2024-01-01,100\n", encoding="utf-8")
        code, _, err = self.run_cli("import", "--from", str(source))
        self.assertEqual(code, 1)
        self.assertIn("필수 컬럼", err)


class SafetyTest(CliTestCase):
    def test_corrupted_file_reports_line_without_traceback(self) -> None:
        self.add("2024-01-15", "expense", "food", "15000")
        with (self.data_dir / TRANSACTIONS_FILE).open("a", encoding="utf-8") as file:
            file.write("{broken json\n")
        code, _, err = self.run_cli("list")
        self.assertEqual(code, 4)
        self.assertIn("2번째 줄", err)
        self.assertNotIn("Traceback", err)

    def test_usage_error_exit_code(self) -> None:
        code, _, err = self.run_cli("list", "--limit", "abc")
        self.assertEqual(code, 2)
        self.assertIn("[오류]", err)

    def test_backup_and_restore(self) -> None:
        self.add("2024-01-15", "expense", "food", "15000")
        code, out, _ = self.run_cli("backup")
        self.assertEqual(code, 0)
        backup_name = Path(out.strip().split()[-1]).name
        self.run_cli("delete", "--id", "1")
        self.assertEqual(self.read_jsonl(TRANSACTIONS_FILE), [])
        code, out, err = self.run_cli("backup", "restore", "--file", backup_name)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.read_jsonl(TRANSACTIONS_FILE)), 1)

    def test_recurring_apply_is_idempotent(self) -> None:
        self.run_cli("recurring", "add", "--day", "31", "--type", "expense", "--category", "rent", "--amount", "500000")
        code, out, _ = self.run_cli("recurring", "apply", "--month", "2024-02")
        self.assertEqual(code, 0)
        self.assertIn("2024-02-29", out)
        _, out, _ = self.run_cli("recurring", "apply", "--month", "2024-02")
        self.assertIn("created=0, skipped=1", out)
        self.assertEqual(len(self.read_jsonl(TRANSACTIONS_FILE)), 1)


class FormatterTest(unittest.TestCase):
    def test_korean_width(self) -> None:
        self.assertEqual(display_width("food"), 4)
        self.assertEqual(display_width("식비"), 4)
        self.assertEqual(pad("식비", 6), "식비  ")


if __name__ == "__main__":
    unittest.main()
