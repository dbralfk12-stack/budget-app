"""공통 관심사 데코레이터: 예외 처리 / 실행 로그 / 실행 시간 측정.

비즈니스 로직(서비스)과 CLI 핸들러에 try/except, 로그, 시간 측정 코드를 반복해서
쓰지 않도록 함수 바깥에서 감싼다. ParamSpec으로 감싼 함수의 시그니처(타입)를 유지한다.
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable
from typing import ParamSpec, TypeVar

from .errors import EXIT_CANCELLED, EXIT_STORAGE, EXIT_UNEXPECTED, AppError
from .formatter import print_error

P = ParamSpec("P")
R = TypeVar("R")

logger = logging.getLogger("budget_app")


def handle_errors(func: Callable[P, int]) -> Callable[P, int]:
    """예외를 스택트레이스 대신 '원인 + 힌트'로 출력하고 0이 아닌 종료 코드를 돌려준다."""

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> int:
        try:
            return func(*args, **kwargs)
        except AppError as exc:
            logger.warning("%s: %s", type(exc).__name__, exc.message)
            print_error(exc.message, exc.hint)
            return exc.exit_code
        except (KeyboardInterrupt, EOFError):
            print()
            print_error("입력이 중단되어 작업을 취소했습니다.", "처음부터 다시 실행해 주세요.")
            return EXIT_CANCELLED
        except UnicodeDecodeError:
            print_error("파일을 UTF-8로 읽을 수 없습니다.", "파일을 UTF-8 인코딩으로 다시 저장한 뒤 시도하세요.")
            return EXIT_STORAGE
        except OSError as exc:
            logger.warning("OSError: %s", exc)
            target = f" ({exc.filename})" if exc.filename else ""
            print_error(
                f"파일을 읽거나 쓰지 못했습니다: {exc.strerror or exc}{target}",
                "경로가 올바른지, 다른 프로그램이 파일을 열고 있지 않은지, 쓰기 권한이 있는지 확인하세요.",
            )
            return EXIT_STORAGE
        except Exception as exc:  # 마지막 안전망: 상세 내용은 로그 파일에만 남긴다.
            logger.exception("예상하지 못한 오류")
            print_error(
                f"예상하지 못한 오류가 발생했습니다: {type(exc).__name__}: {exc}",
                "데이터 폴더의 app.log에 상세 내용이 기록되었습니다. 문제가 반복되면 이 파일을 첨부해 문의하세요.",
            )
            return EXIT_UNEXPECTED

    return wrapper


def log_call(func: Callable[P, R]) -> Callable[P, R]:
    """함수 시작/완료/실패를 로그 파일(app.log)에 남긴다."""

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        name = func.__qualname__
        logger.info("시작 %s", name)
        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            logger.info("실패 %s: %s", name, exc)
            raise
        logger.info("완료 %s", name)
        return result

    return wrapper


def timed(func: Callable[P, R]) -> Callable[P, R]:
    """실행 시간을 측정해 로그로 남긴다. --verbose 옵션이면 화면에도 보인다."""

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        start = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info("실행 시간 %s: %.2fms", func.__qualname__, elapsed_ms)

    return wrapper
