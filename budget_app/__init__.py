"""나만의 용돈 기입장(콘솔 가계부).

실행: python -m budget_app <command> [options]
"""

import logging

__version__ = "1.0.0"

# 라이브러리로 import될 때 로그 설정이 없으면 경고가 출력되지 않도록 한다.
logging.getLogger(__name__).addHandler(logging.NullHandler())
