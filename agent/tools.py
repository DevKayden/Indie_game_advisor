"""LangChain Tool 정의 모듈.

에이전트가 호출할 수 있는 3가지 도구:
1. steam_market_search  — Steam 게임 검색 + 동접자 + 가격
2. youtube_review_search — YouTube 리뷰 영상 스크래핑
3. game_design_rag_search — Chroma 벡터 DB 게임 디자인 문서 검색
"""
import json
import logging
import re

import httpx
from bs4 import BeautifulSoup
from langchain_core.tools import tool

from rag.vectorstore import retriever

logger = logging.getLogger("indie_game_advisor")

# 사운드트랙 및 DLC 필터 키워드
_DLC_KEYWORDS = [
    "soundtrack", "ost", "dlc", "pack", "pass", "upgrade",
    "bundle", "season", "content", "episode", "expansion",
    "preorder", "pre-order", "artbook", "wallpaper",
]


def _is_dlc(name: str) -> bool:
    """게임 이름에 DLC/사운드트랙 키워드가 포함되어 있는지 확인합니다."""
    name_lower = name.lower()
    return any(kw in name_lower for kw in _DLC_KEYWORDS)


async def _fetch_player_count(appid: int, client: httpx.AsyncClient) -> int:
    try:
        resp = await client.get(
            "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/",
            params={"appid": appid},
            timeout=5,
        )
        return resp.json().get("response", {}).get("player_count", 0)
    except Exception:
        return 0


async def _fetch_price(appid: int, client: httpx.AsyncClient) -> str:
    try:
        resp = await client.get(
            "https://store.steampowered.com/api/appdetails",
            params={"appids": appid, "cc": "KR", "l": "korean", "filters": "price_overview"},
            timeout=5,
        )
        data = resp.json().get(str(appid), {})
        if not data.get("success"):
            return "가격 정보 없음"
        po = data.get("data", {}).get("price_overview")
        if not po:
            return "무료"
        final_val = po.get("final", 0) / 100
        currency = po.get("currency", "USD")
        return f"₩{int(final_val):,}" if currency == "KRW" else f"${final_val:.2f}"
    except Exception:
        return "가격 정보 없음"


async def _fetch_reviews(appid: int, client: httpx.AsyncClient) -> list[str]:
    """Steam API를 사용하여 게임의 최신 한국어 리뷰를 수집합니다."""
    try:
        resp = await client.get(
            f"https://store.steampowered.com/appreviews/{appid}",
            params={"json": "1", "language": "korean", "num_per_page": "20"},
            timeout=10,
        )
        data = resp.json()
        reviews = data.get("reviews", [])
        return [r.get("review") for r in reviews if r.get("review")]
    except Exception as e:
        logger.warning("리뷰 수집 실패: %s", e)
        return []


async def _scrape_youtube(game_name: str, client: httpx.AsyncClient) -> list[dict]:
    """YouTube에서 게임 리뷰/분석 영상 3개를 스크래핑합니다."""
    query = f"{game_name} 리뷰 게임플레이"
    encoded = query.replace(" ", "+")
    url = f"https://www.youtube.com/results?search_query={encoded}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    try:
        resp = await client.get(url, headers=headers, timeout=10, follow_redirects=True)
        # ytInitialData JSON 파싱
        match = re.search(r"var ytInitialData\s*=\s*(\{.*?\});\s*</script>", resp.text, re.DOTALL)
        if not match:
            return []
        data = json.loads(match.group(1))
        videos = []
        sections = (
            data.get("contents", {})
            .get("twoColumnSearchResultsRenderer", {})
            .get("primaryContents", {})
            .get("sectionListRenderer", {})
            .get("contents", [])
        )
        for section in sections:
            items = section.get("itemSectionRenderer", {}).get("contents", [])
            for item in items:
                vr = item.get("videoRenderer")
                if not vr:
                    continue
                title_runs = vr.get("title", {}).get("runs", [])
                title = "".join(r["text"] for r in title_runs)
                video_id = vr.get("videoId", "")
                if title and video_id:
                    videos.append({"title": title, "url": f"https://www.youtube.com/watch?v={video_id}"})
                if len(videos) >= 3:
                    break
            if len(videos) >= 3:
                break
        return videos[:3]
    except Exception as e:
        logger.warning("YouTube 스크래핑 실패: %s", e)
        return []


# ──────────────────────────────────────────────────────────────────────────────
# Tool 1: Steam 시장 조사
# ──────────────────────────────────────────────────────────────────────────────

@tool
async def steam_market_search(query: str) -> list:
    """Steam Store에서 인디 게임을 검색하여 유사 게임 목록(이름, 동접자, 가격, 이미지)을 반환합니다.
    DLC와 사운드트랙은 자동으로 제외됩니다.

    Args:
        query: 검색할 영어 게임 키워드 (예: "roguelike action dungeon")
    """
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                "https://store.steampowered.com/api/storesearch",
                params={"term": query, "l": "korean", "cc": "KR"},
                timeout=10,
            )
            items = resp.json().get("items", [])[:12]
        except Exception as e:
            logger.error("Steam 검색 실패: %s", e)
            return []

    # 1차 DLC 필터링
    filtered = [it for it in items if not _is_dlc(it.get("name", ""))]
    top_game = filtered[0] if filtered else (items[0] if items else None)

    if not top_game:
        return []

    results = []
    async with httpx.AsyncClient() as client:
        appid = top_game.get("id")
        name = top_game.get("name", "")
        img = top_game.get("tiny_image", "")
        players = await _fetch_player_count(appid, client)
        price = await _fetch_price(appid, client)
        reviews = await _fetch_reviews(appid, client)
        
        results.append({
            "appid": appid, 
            "name": name, 
            "img": img, 
            "price": price, 
            "players": players,
            "reviews": reviews
        })

    return results


# ──────────────────────────────────────────────────────────────────────────────
# Tool 2: YouTube 리뷰 검색
# ──────────────────────────────────────────────────────────────────────────────

@tool
async def youtube_review_search(game_name: str) -> list:
    """지정한 게임의 YouTube 리뷰·분석 영상 3개를 검색하여 제목과 URL을 반환합니다.

    Args:
        game_name: 검색할 게임 이름 (한국어 또는 영어)
    """
    async with httpx.AsyncClient() as client:
        return await _scrape_youtube(game_name, client)


# ──────────────────────────────────────────────────────────────────────────────
# Tool 3: 게임 디자인 RAG 검색
# ──────────────────────────────────────────────────────────────────────────────

@tool
def game_design_rag_search(query: str) -> str:
    """로컬 게임 디자인 지식 베이스(Chroma DB)에서 관련 문서를 검색합니다.
    장르 특성, 성공 사례 분석, 게임 피드백 원칙, 수익화 전략 등을 포함합니다.

    Args:
        query: 검색할 질문 또는 키워드 (한국어 권장)
    """
    try:
        docs = retriever.invoke(query)
        if not docs:
            return "관련 문서를 찾지 못했습니다."
        results = []
        for i, doc in enumerate(docs, 1):
            source = doc.metadata.get("source", "")
            filename = source.replace("\\", "/").split("/")[-1].replace(".txt", "")
            results.append(f"[문서 {i} — {filename}]\n{doc.page_content.strip()}")
        return "\n\n---\n\n".join(results)
    except Exception as e:
        logger.error("RAG 검색 실패: %s", e)
        return f"검색 중 오류가 발생했습니다: {e}"
