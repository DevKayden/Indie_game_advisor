"""LangGraph StateGraph 조립 및 컴파일 모듈.

그래프 구조:
    START → input_guard
              ├─(error)─→ END
              └─(valid)─→ intent_classifier
                              ├─(market)─→ market_researcher → synthesizer
                              ├─(design)─→ design_advisor   → synthesizer
                              ├─(tech)───→ tech_advisor      → synthesizer
                              └─(general)→ general_responder → synthesizer
                                                               ↓
                                                        context_updater → END

조건부 분기 (conditional edges):
  1. input_guard 이후: valid / invalid 분기
  2. intent_classifier 이후: market / design / tech / general 4방향 분기
"""
import logging
from typing import Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agent.nodes import (
    context_updater_node,
    design_advisor_node,
    general_responder_node,
    input_guard_node,
    intent_classifier_node,
    market_researcher_node,
    synthesizer_node,
    tech_advisor_node,
)
from agent.state import GameConsultState

logger = logging.getLogger("indie_game_advisor")


# ──────────────────────────────────────────────────────────────────────────────
# 조건부 분기 라우터 함수
# ──────────────────────────────────────────────────────────────────────────────

def route_after_guard(state: GameConsultState) -> Literal["valid", "invalid"]:
    """입력 가드레일 결과에 따라 다음 노드를 결정합니다."""
    if state.get("error"):
        return "invalid"
    return "valid"


def route_by_intent(
    state: GameConsultState,
) -> Literal["market", "design", "tech", "general"]:
    """분류된 의도에 따라 전문 노드로 라우팅합니다."""
    intent = state.get("intent", "general")
    if intent in ("market", "design", "tech"):
        return intent
    return "general"


# ──────────────────────────────────────────────────────────────────────────────
# 그래프 빌드 함수
# ──────────────────────────────────────────────────────────────────────────────

def build_graph():
    #LangGraph StateGraph를 조립하고 MemorySaver로 컴파일
    builder = StateGraph(GameConsultState)

    # ── 노드 등록 ──────────────────────────────────────────────────────────────
    builder.add_node("input_guard", input_guard_node)
    builder.add_node("intent_classifier", intent_classifier_node)
    builder.add_node("market_researcher", market_researcher_node)
    builder.add_node("design_advisor", design_advisor_node)
    builder.add_node("tech_advisor", tech_advisor_node)
    builder.add_node("general_responder", general_responder_node)
    builder.add_node("synthesizer", synthesizer_node)
    builder.add_node("context_updater", context_updater_node)

    # ── 엣지 연결 ──────────────────────────────────────────────────────────────
    # 시작점
    builder.add_edge(START, "input_guard")

    # [조건부 분기 1] 입력 검증 결과에 따라 분기
    builder.add_conditional_edges(
        "input_guard",
        route_after_guard,
        {"valid": "intent_classifier", "invalid": END},
    )

    # [조건부 분기 2] 의도에 따라 4방향 분기
    builder.add_conditional_edges(
        "intent_classifier",
        route_by_intent,
        {
            "market": "market_researcher",
            "design": "design_advisor",
            "tech": "tech_advisor",
            "general": "general_responder",
        },
    )

    # 모든 전문 노드 → synthesizer
    builder.add_edge("market_researcher", "synthesizer")
    builder.add_edge("design_advisor", "synthesizer")
    builder.add_edge("tech_advisor", "synthesizer")
    builder.add_edge("general_responder", "synthesizer")

    # synthesizer → context_updater → END
    builder.add_edge("synthesizer", "context_updater")
    builder.add_edge("context_updater", END)

    # MemorySaver: 세션별 대화 이력 및 game_context 영속 보관
    memory = MemorySaver()
    graph = builder.compile(checkpointer=memory)

    logger.info("LangGraph StateGraph 컴파일 완료.")
    logger.info("그래프 구조:\n%s", graph.get_graph().draw_mermaid())

    return graph


# 모듈 로드 시 그래프 인스턴스 생성
agent_graph = build_graph()
