"""LangGraph 노드 함수 정의 모듈.

각 노드는 GameConsultState를 받아 업데이트된 필드 딕셔너리를 반환합니다.
모든 노드는 stream_log()를 통해 실시간 SSE 로그를 전송합니다.
"""
import logging
from typing import Literal

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from agent.context_vars import stream_log
from agent.state import GameConsultState
from agent.tools import game_design_rag_search, steam_market_search, youtube_review_search
from middleware.guard import validate_input
from middleware.logger import log_node

logger = logging.getLogger("indie_game_advisor")


# ──────────────────────────────────────────────────────────────────────────────
# OutputParser 모델 (평가 요구사항: PydanticOutputParser 활용)
# ──────────────────────────────────────────────────────────────────────────────

class IntentClassification(BaseModel):
    """사용자 입력의 의도를 분류하는 구조화 출력 모델."""

    intent: Literal["market", "design", "tech", "general"] = Field(
        description=(
            "사용자 요청의 의도 분류.\n"
            "- market: 특정 게임의 시장 조사, 스팀 유저 리뷰 분석, 경쟁작 분석, 동접자/가격 관련\n"
            "- design: 게임 메카닉, 장르 특성, 디자인 패턴, 게임플레이 요소\n"
            "- tech: 기술 스택, 게임 엔진, 서버 구조, 개발 환경, 프레임워크\n"
            "- general: 인사, 일반 대화, 위 세 가지에 해당하지 않는 것"
        )
    )
    reasoning: str = Field(description="분류 근거를 한 문장으로 설명")
    keywords: list[str] = Field(
        description="사용자 입력에서 추출한 핵심 키워드 목록", default_factory=list
    )
    game_info: str = Field(
        description="사용자가 언급한 게임 관련 정보 (장르, 스타일, 특징 등). 없으면 빈 문자열",
        default="",
    )

class ReviewAnalysis(BaseModel):
    """Steam 유저 리뷰 기반 장단점 분석 모델."""
    pros: list[str] = Field(description="유저 리뷰에서 도출된 게임의 긍정적 평가 (장점) 목록")
    cons: list[str] = Field(description="유저 리뷰에서 도출된 게임의 부정적 평가 또는 아쉬운 점 (단점) 목록")


# ──────────────────────────────────────────────────────────────────────────────
# 노드 1: 입력 가드레일 (Middleware)
# ──────────────────────────────────────────────────────────────────────────────

@log_node("input_guard")
async def input_guard_node(state: GameConsultState) -> dict:
    """입력 검증 미들웨어 노드. 유효하지 않으면 error를 설정하고 종료."""
    await stream_log("[Safety Check] 입력 검증 미들웨어 실행 중...", "safety")

    # 매 요청마다 일시적 필드를 초기화합니다
    reset = {"market_data": [], "rag_results": "", "final_response": "", "error": "", "intent": ""}

    messages = state.get("messages", [])
    if not messages:
        reset["error"] = "메시지가 없습니다."
        await stream_log("[Safety Check Failure] 메시지가 없습니다.", "error")
        return reset

    user_input = messages[-1].content if hasattr(messages[-1], "content") else str(messages[-1])
    is_valid, error_msg = validate_input(user_input)

    if not is_valid:
        reset["error"] = error_msg
        await stream_log(f"[Safety Check Failure] {error_msg}", "error")
        return reset

    await stream_log("[Safety Check Pass] 검증 통과. 에이전트를 시작합니다.", "safety")
    return reset


# ──────────────────────────────────────────────────────────────────────────────
# 노드 2: 의도 분류기 (PydanticOutputParser 활용)
# ──────────────────────────────────────────────────────────────────────────────

@log_node("intent_classifier")
async def intent_classifier_node(state: GameConsultState) -> dict:
    """LLM + PydanticOutputParser로 사용자 의도를 분류합니다."""
    await stream_log("[Agent] 사용자 의도를 분석하고 있습니다...", "agent")

    messages = state.get("messages", [])
    user_input = messages[-1].content if hasattr(messages[-1], "content") else ""
    game_context = state.get("game_context", "") or ""

    parser = PydanticOutputParser(pydantic_object=IntentClassification)

    prompt = PromptTemplate(
        template=(
            "당신은 인디 게임 기획 상담 AI의 의도 분류기입니다.\n"
            "사용자의 메시지를 분석하여 정확히 분류하세요.\n\n"
            "현재까지 파악된 게임 맥락: {game_context}\n\n"
            "{format_instructions}\n\n"
            "사용자 메시지: {user_message}"
        ),
        input_variables=["user_message", "game_context"],
        partial_variables={"format_instructions": parser.get_format_instructions()},
    )

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.0)
    chain = prompt | llm | parser

    try:
        result: IntentClassification = await chain.ainvoke(
            {"user_message": user_input, "game_context": game_context or "아직 없음"}
        )
    except Exception as e:
        logger.warning("의도 분류 실패, general로 폴백: %s", e)
        result = IntentClassification(intent="general", reasoning="분류 실패 폴백", keywords=[], game_info="")

    await stream_log(
        f"[Agent Reasoning] 의도: '{result.intent}' — {result.reasoning}", "agent"
    )

    # game_context 누적
    new_context = game_context
    if result.game_info:
        new_context = (game_context + "\n" + result.game_info).strip() if game_context else result.game_info

    return {"intent": result.intent, "game_context": new_context}


# ──────────────────────────────────────────────────────────────────────────────
# 노드 3: 시장 조사 (Steam + YouTube Tool 활용)
# ──────────────────────────────────────────────────────────────────────────────

@log_node("market_researcher")
async def market_researcher_node(state: GameConsultState) -> dict:
    """Steam과 YouTube 도구를 사용하여 시장 데이터를 수집합니다."""
    await stream_log("[Tool Call] Steam 시장 검색 도구를 실행합니다...", "tool_start")

    messages = state.get("messages", [])
    user_input = messages[-1].content if hasattr(messages[-1], "content") else ""
    game_context = state.get("game_context", "") or ""

    # 검색 키워드 추출
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.0)
    kw_prompt = ChatPromptTemplate.from_template(
        "다음 사용자의 질문과 기획 맥락에서 Steam 게임 검색에 사용할 핵심 영어 키워드를 1~3개만 추출하세요.\n"
        "특정 게임 이름이 언급되었다면 그 게임의 영문명을 최우선으로 추출하세요 (예: 하데스 -> Hades, 다키스트 던전 -> Darkest Dungeon).\n"
        "장르라면 대표적인 영어 장르명을 추출하세요 (예: 로그라이크 -> roguelike).\n"
        "'RPG game creation' 같은 문장이 아닌, 실제 Steam 검색에 유효한 고유명사나 장르명만 출력하세요.\n"
        "키워드만 띄어쓰기로 구분하여 출력하세요.\n\n"
        "사용자 질문: {user_input}\n"
        "기획 맥락: {game_context}"
    )
    kw_chain = kw_prompt | llm | StrOutputParser()
    search_kw = await kw_chain.ainvoke({"user_input": user_input, "game_context": game_context or "없음"})
    search_kw = search_kw.strip()
    await stream_log(f"[Agent Reasoning] Steam 검색 키워드: '{search_kw}'", "agent")

    # Steam 검색 도구 호출
    market_data: list = await steam_market_search.ainvoke({"query": search_kw})
    await stream_log(
        f"[Tool Response] Steam 검색 완료. {len(market_data)}개의 유사 게임 발견.", "tool_end"
    )

    # 최상위 게임 YouTube 리뷰 검색
    if market_data:
        top_name = market_data[0]["name"]
        await stream_log(f"[Tool Call] YouTube 리뷰 검색 중: {top_name}", "tool_start")
        yt_videos: list = await youtube_review_search.ainvoke({"game_name": top_name})
        market_data[0]["youtube_videos"] = yt_videos
        await stream_log(f"[Tool Response] YouTube 영상 {len(yt_videos)}개 연동 완료.", "tool_end")

    return {"market_data": market_data}


# ──────────────────────────────────────────────────────────────────────────────
# 노드 4: 게임 디자인 어드바이저 (RAG Tool 활용)
# ──────────────────────────────────────────────────────────────────────────────

@log_node("design_advisor")
async def design_advisor_node(state: GameConsultState) -> dict:
    """게임 디자인 RAG 도구를 사용하여 관련 문서를 검색합니다."""
    await stream_log("[Tool Call] 게임 디자인 지식 베이스를 검색합니다...", "tool_start")

    messages = state.get("messages", [])
    user_input = messages[-1].content if hasattr(messages[-1], "content") else ""
    game_context = state.get("game_context", "") or ""

    query = f"{user_input} {game_context}".strip()
    rag_results: str = game_design_rag_search.invoke({"query": query})

    await stream_log("[Tool Response] 관련 게임 디자인 문서 검색 완료.", "tool_end")
    return {"rag_results": rag_results}


# ──────────────────────────────────────────────────────────────────────────────
# 노드 5: 기술 스택 어드바이저
# ──────────────────────────────────────────────────────────────────────────────

@log_node("tech_advisor")
async def tech_advisor_node(state: GameConsultState) -> dict:
    """기획 맥락을 바탕으로 게임 기술 스택 추천 보고서를 작성합니다."""
    await stream_log("[Agent] 기술 스택 분석 엔진을 가동합니다...", "agent")

    messages = state.get("messages", [])
    user_input = messages[-1].content if hasattr(messages[-1], "content") else ""
    game_context = state.get("game_context", "") or ""

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3)
    tech_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "당신은 게임 테크니컬 아키텍트입니다. 인디 게임 기획자에게 최적의 기술 스택을 추천해 주세요.\n\n"
                "현재까지 파악된 게임 맥락: {game_context}\n\n"
                "아래 항목을 포함하여 한국어 마크다운으로 작성하세요:\n"
                "- **게임 엔진 추천** (Unity / Unreal / Godot 중 선택 및 이유)\n"
                "- **클라이언트 기술** (언어, 렌더링 방식, 에셋 관리)\n"
                "- **서버 구조** (싱글플레이/멀티플레이에 따라 선택)\n"
                "- **데이터 관리** (세이브 데이터, 리더보드 등)\n"
                "- **개발 시 주의사항**\n\n"
                "이모지 없이 간결하고 전문적으로 작성하세요.",
            ),
            ("human", "{user_message}"),
        ]
    )
    chain = tech_prompt | llm | StrOutputParser()
    report = await chain.ainvoke(
        {"game_context": game_context or "아직 없음", "user_message": user_input}
    )

    await stream_log("[Agent] 기술 스택 보고서 작성 완료.", "agent")
    return {"rag_results": report}


# ──────────────────────────────────────────────────────────────────────────────
# 노드 6: 일반 응답
# ──────────────────────────────────────────────────────────────────────────────

@log_node("general_responder")
async def general_responder_node(state: GameConsultState) -> dict:
    """대화 이력과 게임 맥락을 바탕으로 일반 응답을 생성합니다."""
    await stream_log("[Agent] 응답을 생성하고 있습니다...", "agent")

    game_context = state.get("game_context", "") or ""
    all_messages = state.get("messages", [])

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7)
    system_msg = SystemMessage(
        content=(
            "당신은 친절하고 전문적인 인디 게임 기획 상담 AI입니다.\n"
            "게임 개발, 기획, 디자인, 시장에 대해 폭넓게 도움을 드립니다.\n\n"
            f"현재까지 파악된 게임 맥락: {game_context or '아직 없음'}\n\n"
            "이모지 없이 자연스러운 한국어로 대화하세요."
        )
    )
    response = await llm.ainvoke([system_msg] + list(all_messages))
    return {"final_response": response.content}


# ──────────────────────────────────────────────────────────────────────────────
# 노드 7: 최종 응답 합성기
# ──────────────────────────────────────────────────────────────────────────────

@log_node("synthesizer")
async def synthesizer_node(state: GameConsultState) -> dict:
    """수집된 데이터(시장 조사, RAG 결과)를 종합하여 최종 응답을 생성합니다."""
    # general_responder가 이미 응답을 생성한 경우 패스
    if state.get("final_response"):
        return {}

    await stream_log("[Agent] 수집된 데이터를 종합하여 최종 답변을 작성합니다...", "agent")

    messages = state.get("messages", [])
    user_input = messages[-1].content if hasattr(messages[-1], "content") else ""
    game_context = state.get("game_context", "") or ""
    market_data: list = state.get("market_data", [])
    rag_results: str = state.get("rag_results", "") or ""

    context_parts = [f"사용자 질문: {user_input}"]
    if game_context:
        context_parts.append(f"기획 중인 게임 맥락: {game_context}")
    intent = state.get("intent", "general")
    
    if intent == "market" and market_data:
        target_game = market_data[0]
        reviews_text = "\n".join([f"- {r}" for r in target_game.get("reviews", [])])
        
        parser = PydanticOutputParser(pydantic_object=ReviewAnalysis)
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.0)
        
        synth_prompt = ChatPromptTemplate.from_messages([
            ("system", "당신은 스팀 게임 리뷰 분석가입니다.\n수집된 유저 리뷰를 분석하여 게임의 장단점을 명확하고 간결하게 추출하세요.\n절대 이모지를 사용하지 마세요. 각 항목은 마크다운 헤더(##, ###)를 사용하여 크기를 키워주세요.\n\n{format_instructions}"),
            ("human", f"게임 이름: {target_game['name']}\n\n유저 리뷰:\n{reviews_text}")
        ])
        
        try:
            chain = synth_prompt | llm | parser
            analysis_result = await chain.ainvoke({"format_instructions": parser.get_format_instructions()})
            
            md_lines = [f"## **{target_game['name']}** 스팀 유저 리뷰 분석\n"]
            md_lines.append("### 긍정적 평가 (장점)")
            for pro in analysis_result.pros:
                md_lines.append(f"- {pro}")
                
            md_lines.append("\n### 부정적 평가 / 아쉬운 점 (단점)")
            for con in analysis_result.cons:
                md_lines.append(f"- {con}")
                
            final_response = "\n".join(md_lines)
            await stream_log("[Agent] 리뷰 분석 완료. 답변이 준비되었습니다.", "finish")
            return {"final_response": final_response}
        except Exception as e:
            logger.error("리뷰 분석 파싱 실패: %s", e)
            # 실패 시 아래의 일반 종합 로직으로 fallback 진행

    # Market이 아니거나 리뷰 수집 실패, 혹은 일반/설계/기술 인텐트의 경우 기본 로직 실행
    if intent == "market" and not market_data:
        context_parts.append("Steam 시장 조사 결과: 검색 도구에서 일치하는 게임을 찾지 못했습니다. 대상 게임을 명확히 특정할 수 있도록 다른 이름이나 정확한 영문명으로 다시 질문해 달라고 사용자에게 요청해주세요. (임의로 다른 게임을 추천하지 마세요.)")
    elif market_data:
        lines = [
            f"- {g['name']}: 동접자 {g.get('players', 0):,}명, 가격 {g.get('price', 'N/A')}"
            for g in market_data
        ]
        context_parts.append("Steam 시장 조사 결과:\n" + "\n".join(lines))
    if rag_results:
        context_parts.append(f"게임 디자인 참고 자료:\n{rag_results}")

    combined = "\n\n".join(context_parts)

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.4)
    synth_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "당신은 인디 게임 기획 전문 컨설턴트입니다.\n"
                "수집된 데이터를 바탕으로 기획자에게 실질적인 인사이트와 조언을 제공하세요.\n"
                "이모지 없이 친절하고 전문적인 어조로 마크다운 형식으로 작성하세요.",
            ),
            ("human", "{context}"),
        ]
    )
    chain = synth_prompt | llm | StrOutputParser()
    final_response = await chain.ainvoke({"context": combined})

    await stream_log("[Agent] 분석 완료. 답변이 준비되었습니다.", "finish")
    return {"final_response": final_response}


# ──────────────────────────────────────────────────────────────────────────────
# 노드 8: 게임 맥락 업데이터 (메모리 갱신)
# ──────────────────────────────────────────────────────────────────────────────

@log_node("context_updater")
async def context_updater_node(state: GameConsultState) -> dict:
    """대화에서 추출한 게임 정보를 game_context에 누적합니다."""
    intent = state.get("intent", "general")
    if intent == "general":
        return {}  # 일반 대화에서는 맥락 업데이트 불필요

    messages = state.get("messages", [])
    user_input = messages[-1].content if hasattr(messages[-1], "content") else ""
    current_context = state.get("game_context", "") or ""

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.0)
    update_prompt = ChatPromptTemplate.from_template(
        "기존 게임 기획 맥락과 새로운 대화를 합쳐서 3~5문장으로 업데이트된 게임 맥락을 작성하세요.\n\n"
        "기존 맥락: {current_context}\n"
        "새로운 대화: {new_message}\n\n"
        "업데이트된 게임 맥락만 출력하세요:"
    )
    chain = update_prompt | llm | StrOutputParser()

    try:
        new_context = await chain.ainvoke(
            {"current_context": current_context or "없음", "new_message": user_input}
        )
        return {"game_context": new_context.strip()}
    except Exception as e:
        logger.warning("맥락 업데이트 실패: %s", e)
        return {}
