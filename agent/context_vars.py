"""SSE 스트리밍을 위한 ContextVar 모듈.

각 HTTP 요청의 asyncio 태스크에 큐를 주입하여,
LangGraph 노드 내부에서도 SSE 로그를 실시간 전송할 수 있음.
"""
import asyncio
import json
from contextvars import ContextVar

# 요청마다 새 Queue가 설정.
# asyncio.create_task()는 현재 컨텍스트를 복사하므로
# 태스크 내부의 모든 노드에서 이 큐에 접근 가능.
log_queue_ctx: ContextVar[asyncio.Queue | None] = ContextVar(
    "log_queue_ctx", default=None
)


async def stream_log(message: str, msg_type: str = "log") -> None:
    """SSE 큐에 로그 메시지를 푸시.

    Args:
        message: 사용자에게 보여줄 로그 텍스트
        msg_type: 메시지 유형 (log | agent | safety | tool_start | tool_end |
                  finish | error)
    """
    queue = log_queue_ctx.get()
    if queue is not None:
        payload = json.dumps({"type": msg_type, "message": message}, ensure_ascii=False)
        await queue.put(payload)
        await asyncio.sleep(0.01)  # 이벤트 루프에 제어권 반환
