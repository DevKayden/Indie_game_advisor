"""구조화 로깅 미들웨어."""
import logging
import time
from functools import wraps

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("indie_game_advisor")


def log_node(node_name: str):
    """LangGraph 노드 함수에 적용하는 실행 시간 및 상태 로깅 데코레이터."""

    def decorator(func):
        @wraps(func)
        async def wrapper(state, *args, **kwargs):
            start = time.perf_counter()
            logger.info("[NODE START] %s", node_name)
            try:
                result = await func(state, *args, **kwargs)
                elapsed = time.perf_counter() - start
                logger.info("[NODE END]   %s | %.2fs", node_name, elapsed)
                return result
            except Exception as exc:
                elapsed = time.perf_counter() - start
                logger.error(
                    "[NODE ERROR] %s | %.2fs | %s", node_name, elapsed, exc, exc_info=True
                )
                raise

        return wrapper

    return decorator
