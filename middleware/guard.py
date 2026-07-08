"""입력 검증 가드레일 미들웨어."""
import re


# 차단 패턴 목록 (프롬프트 인젝션, 개인정보 요청 등)
_BLOCKED_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?i)ignore\s+previous\s+instructions?"),
    re.compile(r"(?i)forget\s+(all|your)\s+(previous|prior)"),
    re.compile(r"(?i)(api|secret)\s*key"),
    re.compile(r"(?i)password"),
    re.compile(r"(?i)주민\s*등록"),
]


def validate_input(user_input: str) -> tuple[bool, str]:
    """사용자 입력값을 검증합니다.

    Returns:
        (is_valid: bool, error_message: str)
        is_valid가 False이면 error_message에 사유가 담깁니다.
    """
    if not user_input or not user_input.strip():
        return False, "입력값이 비어있습니다."

    text = user_input.strip()

    if len(text) < 2:
        return False, "입력값이 너무 짧습니다. (최소 2자 이상 입력해 주세요)"

    if len(text) > 1500:
        return False, "입력값이 너무 깁니다. (최대 1500자)"

    for pattern in _BLOCKED_PATTERNS:
        if pattern.search(text):
            return False, "허용되지 않는 입력 패턴이 감지되었습니다."

    return True, ""
