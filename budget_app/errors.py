"""사용자에게 보여줄 오류 정의.

모든 예상 가능한 오류는 AppError를 상속하며 '원인(message)'과 '해결 힌트(hint)'를 함께 가진다.
CLI는 스택트레이스 대신 이 두 가지만 출력하고, exit_code로 종료한다.
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_ERROR = 1          # 입력 검증 실패, 충돌 등 일반 오류
EXIT_USAGE = 2          # 명령/옵션 사용법 오류(argparse)
EXIT_NOT_FOUND = 3      # 대상 데이터(id, 파일 등)가 없음
EXIT_STORAGE = 4        # 저장 파일 읽기/쓰기 실패, 파일 손상
EXIT_UNEXPECTED = 10    # 예상하지 못한 내부 오류
EXIT_CANCELLED = 130    # 사용자가 Ctrl+C / 입력 종료(EOF)로 중단


class AppError(Exception):
    """사용자에게 원인과 힌트를 보여주는 오류의 기반 클래스."""

    exit_code: int = EXIT_ERROR

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class ValidationError(AppError):
    """입력값 형식/범위가 올바르지 않음."""


class ConflictError(AppError):
    """이미 존재하거나, 다른 데이터가 사용 중이라 처리할 수 없음."""


class NotFoundError(AppError):
    """요청한 데이터가 존재하지 않음."""

    exit_code = EXIT_NOT_FOUND


class StorageError(AppError):
    """저장 파일이 손상되었거나 읽고 쓸 수 없음."""

    exit_code = EXIT_STORAGE
