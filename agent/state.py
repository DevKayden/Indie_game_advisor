from typing import Annotated
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages


class GameConsultState(TypedDict):
    """인디 게임 기획 상담 에이전트의 전체 상태를 정의합니다."""

    # 전체 대화 이력 — add_messages 리듀서가 새 메시지를 이전 목록에 추가합니다
    messages: Annotated[list, add_messages]

    # 누적되는 게임 기획 맥락 (예: "로그라이크 액션, 2D 픽셀 스타일, 싱글플레이")
    game_context: str

    # intent_classifier가 분류한 현재 요청 의도
    # "market" | "design" | "tech" | "general"
    intent: str

    # Steam 시장 조사 결과 (game card 목록)
    market_data: list

    # RAG 검색 결과 또는 기술 스택 분석 리포트 텍스트
    rag_results: str

    # 최종 사용자에게 전달할 AI 응답
    final_response: str

    # 에러 메시지 (입력 검증 실패 등)
    error: str
