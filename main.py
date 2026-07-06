"""FastAPI 메인 서버 모듈.

엔드포인트:
  GET  /          → index.html (채팅 UI)
  POST /api/chat  → SSE 스트리밍 채팅 (LangGraph 에이전트 실행)
  GET  /api/health → 서버 상태 확인
"""
import asyncio
import json
import logging
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from agent.context_vars import log_queue_ctx
from agent.graph import agent_graph

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("indie_game_advisor")

# ──────────────────────────────────────────────────────────────────────────────
# FastAPI 앱 초기화
# ──────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="인디 게임 기획 AI 어드바이저", version="1.0.0")

PUBLIC_DIR = Path(__file__).parent / "public"
app.mount("/static", StaticFiles(directory=str(PUBLIC_DIR)), name="static")


# ──────────────────────────────────────────────────────────────────────────────
# 요청 모델
# ──────────────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: str = ""


# ──────────────────────────────────────────────────────────────────────────────
# 라우트
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/")
async def serve_index():
    return FileResponse(str(PUBLIC_DIR / "index.html"))


@app.get("/style.css")
async def serve_css():
    return FileResponse(str(PUBLIC_DIR / "style.css"), media_type="text/css")


@app.get("/app.js")
async def serve_js():
    return FileResponse(str(PUBLIC_DIR / "app.js"), media_type="application/javascript")


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "indie_game_advisor"}


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    """LangGraph 에이전트를 실행하고 SSE로 로그 및 결과를 스트리밍합니다."""
    session_id = req.session_id.strip() or str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()

    # ContextVar에 큐 주입 — create_task 시 컨텍스트가 복사되어
    # 백그라운드 태스크 내 모든 노드에서 queue에 접근 가능합니다
    token = log_queue_ctx.set(queue)

    async def run_agent():
        try:
            config = {"configurable": {"thread_id": session_id}}
            inputs = {"messages": [HumanMessage(content=req.message)]}

            result = await agent_graph.ainvoke(inputs, config=config)

            error = result.get("error", "")
            if error:
                payload = json.dumps({"type": "error", "message": error}, ensure_ascii=False)
                await queue.put(payload)
            else:
                final_payload = json.dumps(
                    {
                        "type": "result",
                        "response": result.get("final_response", ""),
                        "market_data": result.get("market_data", []),
                        "rag_results": result.get("rag_results", ""),
                        "session_id": session_id,
                    },
                    ensure_ascii=False,
                )
                await queue.put(final_payload)

        except Exception as exc:
            logger.error("에이전트 실행 오류: %s", exc, exc_info=True)
            err_payload = json.dumps(
                {"type": "error", "message": f"에이전트 오류: {exc}"}, ensure_ascii=False
            )
            await queue.put(err_payload)
        finally:
            await queue.put(None)  # 스트림 종료 신호
            try:
                log_queue_ctx.reset(token)
            except ValueError:
                pass  # asyncio 태스크는 컨텍스트 복사본을 가지므로 무시

    asyncio.create_task(run_agent())

    async def event_generator():
        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"data: {item}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ──────────────────────────────────────────────────────────────────────────────
# 서버 실행
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=3000, reload=False)
