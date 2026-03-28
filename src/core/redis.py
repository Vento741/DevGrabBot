"""Redis клиент для очередей и кэширования."""
import json

import redis.asyncio as aioredis

from src.core.config import Settings

QUEUE_NEW_ORDERS = "devgrab:new_orders"
QUEUE_ANALYZED = "devgrab:analyzed"
SENT_ORDERS_SET = "devgrab:sent_order_ids"
PARSER_PAUSED_KEY = "devgrab:parser:paused"


class RedisClient:
    def __init__(self, settings: Settings):
        self.redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    async def push_order(self, order_data: dict) -> None:
        """Поместить новый заказ в очередь на анализ."""
        await self.redis.rpush(QUEUE_NEW_ORDERS, json.dumps(order_data, ensure_ascii=False))

    async def pop_order(self) -> dict | None:
        """Извлечь заказ из очереди на анализ."""
        data = await self.redis.lpop(QUEUE_NEW_ORDERS)
        return json.loads(data) if data else None

    async def push_analyzed(self, analysis_data: dict) -> None:
        """Поместить результат анализа в очередь уведомлений."""
        await self.redis.rpush(QUEUE_ANALYZED, json.dumps(analysis_data, ensure_ascii=False))

    async def pop_analyzed(self) -> dict | None:
        """Извлечь результат анализа из очереди уведомлений."""
        data = await self.redis.lpop(QUEUE_ANALYZED)
        return json.loads(data) if data else None

    async def is_order_sent(self, external_id: str) -> bool:
        """Проверить, был ли заказ уже обработан."""
        return await self.redis.sismember(SENT_ORDERS_SET, external_id)

    async def mark_order_sent(self, external_id: str) -> None:
        """Пометить заказ как обработанный."""
        await self.redis.sadd(SENT_ORDERS_SET, external_id)

    async def get_queue_length(self, queue: str = QUEUE_NEW_ORDERS) -> int:
        """Получить длину очереди."""
        return await self.redis.llen(queue)

    async def is_parser_paused(self) -> bool:
        """Проверить, приостановлен ли парсер (PM недоступен)."""
        return bool(await self.redis.exists(PARSER_PAUSED_KEY))

    async def set_parser_paused(self) -> None:
        """Поставить парсер на паузу."""
        await self.redis.set(PARSER_PAUSED_KEY, "1")

    async def set_parser_resumed(self) -> None:
        """Снять паузу парсера."""
        await self.redis.delete(PARSER_PAUSED_KEY)

    async def close(self) -> None:
        await self.redis.aclose()
