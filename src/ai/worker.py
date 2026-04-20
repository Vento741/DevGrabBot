"""AI-воркер: забирает заказы из Redis, анализирует через AI, сохраняет в БД."""
import asyncio
import logging

import httpx
from sqlalchemy import select

from src.core.config import Settings
from src.core.database import create_engine, create_session_factory
from src.core.models import AiAnalysis, Order, OrderStatus
from src.core.redis import RedisClient
from src.ai.openrouter import OpenRouterClient
from src.ai.analyzer import OrderAnalyzer
from src.ai.prompts.analyze import SYSTEM_PROMPT as ANALYZE_PROMPT
from src.core.settings_service import get_config_setting, get_prompt

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAYS = [30, 60, 120]  # секунды между retry при 429


async def run_ai_worker(settings: Settings):
    """Бесконечный цикл: Redis → AI-анализ → БД → очередь уведомлений."""
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    redis = RedisClient(settings)
    ai_client = OpenRouterClient(
        api_key=settings.openrouter_api_key,
        model=settings.openrouter_model,
    )
    analyzer = OrderAnalyzer(ai_client)

    logger.info("AI worker запущен")

    try:
        while True:
            order_data = await redis.pop_order()
            if not order_data:
                await asyncio.sleep(5)
                continue

            external_id = order_data.get("external_id", "?")
            logger.info(f"Обработка заказа {external_id}...")

            try:
                async with session_factory() as session:
                    # Проверяем дубликат
                    existing = await session.execute(
                        select(Order).where(Order.external_id == external_id)
                    )
                    if existing.scalar_one_or_none():
                        logger.info(f"Заказ {external_id} уже в БД, пропускаем")
                        continue

                    # Загружаем актуальный промпт из DB (fallback на файловый)
                    custom_prompt = await get_prompt(session, "analyze")
                    analyze_prompt = custom_prompt if custom_prompt is not None else ANALYZE_PROMPT

                    # Загружаем актуальную модель из DB (fallback на settings)
                    current_model = await get_config_setting(session, "openrouter_model", settings)
                    if current_model != ai_client.model:
                        ai_client.model = current_model
                        logger.info(f"AI-модель обновлена из DB: {current_model}")

                    # Сохраняем заказ
                    order = Order(
                        external_id=external_id,
                        platform=order_data.get("platform", "profiru"),
                        title=order_data.get("title", ""),
                        description=order_data.get("description", ""),
                        budget=order_data.get("budget"),
                        response_price=order_data.get("response_price"),
                        materials=order_data.get("materials"),
                        location=order_data.get("location"),
                        raw_text=order_data.get("raw_text", ""),
                        status=OrderStatus.analyzing,
                    )
                    session.add(order)
                    await session.flush()

                    # AI-анализ с retry при 429
                    analysis_result = None
                    for attempt in range(MAX_RETRIES):
                        try:
                            analysis_result = await analyzer.analyze_order(
                                order.raw_text,
                                system_prompt=analyze_prompt,
                            )
                            break
                        except httpx.HTTPStatusError as e:
                            if e.response.status_code == 429:
                                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                                logger.warning(
                                    f"Заказ {external_id}: 429 rate limit "
                                    f"(попытка {attempt + 1}/{MAX_RETRIES}), "
                                    f"ждём {delay}с..."
                                )
                                await asyncio.sleep(delay)
                            else:
                                raise

                    # Все retry исчерпаны — возвращаем заказ в очередь
                    if analysis_result is None:
                        await session.rollback()
                        await redis.push_order(order_data)
                        logger.error(
                            f"Заказ {external_id}: 429 после {MAX_RETRIES} попыток, "
                            f"возвращён в очередь"
                        )
                        await asyncio.sleep(RETRY_DELAYS[-1])
                        continue

                    # Расширенные данные (вопросы, пожелания, риски)
                    extra_data = {
                        k: analysis_result.get(k)
                        for k in (
                            "client_requirements",
                            "client_budget_stated",
                            "client_budget_text",
                            "client_deadline_stated",
                            "client_deadline_text",
                            "questions_to_client",
                            "risks",
                        )
                        if analysis_result.get(k) is not None
                    }

                    # Сохраняем анализ
                    ai_analysis = AiAnalysis(
                        order_id=order.id,
                        summary=analysis_result.get("summary", ""),
                        stack=analysis_result.get("stack", []),
                        price_min=analysis_result.get("price_min"),
                        price_max=analysis_result.get("price_max"),
                        timeline_days=str(analysis_result.get("timeline_days", "")),
                        relevance_score=analysis_result.get("relevance_score", 0),
                        complexity=analysis_result.get("complexity", "medium"),
                        response_draft=analysis_result.get("response_draft", ""),
                        model_used=ai_client.model,
                        extra_data=extra_data or None,
                    )
                    session.add(ai_analysis)

                    order.status = OrderStatus.reviewed
                    await session.commit()

                    # В очередь уведомлений (данные парсера + AI-анализ)
                    await redis.push_analyzed({
                        "order_id": order.id,
                        "external_id": external_id,
                        "title": order.title,
                        "budget": order_data.get("budget", ""),
                        "location": order_data.get("location", ""),
                        "work_format": order_data.get("work_format", ""),
                        "schedule": order_data.get("schedule", ""),
                        "client_name": order_data.get("client_name", ""),
                        "response_price": order_data.get("response_price"),
                        "materials": order_data.get("materials"),
                        "last_update_date": order_data.get("last_update_date"),
                        "analysis": analysis_result,
                    })

                    logger.info(
                        f"Заказ {external_id} проанализирован: "
                        f"relevance={analysis_result.get('relevance_score')}%"
                    )

            except Exception:
                logger.exception(f"Ошибка при обработке заказа {external_id}")

    finally:
        await ai_client.close()
        await redis.close()
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    asyncio.run(run_ai_worker(settings))
