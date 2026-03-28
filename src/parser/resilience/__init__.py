"""Resilience Layer — защита парсера от бана.

Компоненты:
- CircuitBreaker — остановка при каскадных ошибках
- AlertService — уведомления в Telegram с дедупликацией
- TokenManager — кэширование токена в Redis + backoff авторизации
- RequestScheduler — jitter + адаптивные интервалы по времени суток
- HealthMonitor — метрики и статус парсера в Redis
"""

from src.parser.resilience.circuit_breaker import CircuitBreaker, CircuitState
from src.parser.resilience.alert_service import AlertService
from src.parser.resilience.token_manager import TokenManager
from src.parser.resilience.request_scheduler import RequestScheduler
from src.parser.resilience.health import HealthMonitor

__all__ = [
    "CircuitBreaker",
    "CircuitState",
    "AlertService",
    "TokenManager",
    "RequestScheduler",
    "HealthMonitor",
]
