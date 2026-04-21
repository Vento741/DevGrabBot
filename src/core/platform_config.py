"""Pydantic-схемы конфигурации платформенных фильтров (v2.5)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class ProfiruFilters(BaseModel):
    """Platform-specific фильтры Profi.ru."""
    max_response_price: int | None = Field(None, ge=0, description="Макс. цена отклика (руб.)")
    only_remote: bool = False
    exclude_free: bool = False


class KworkFilters(BaseModel):
    """Platform-specific фильтры Kwork."""
    category_ids: list[int] = Field(default_factory=lambda: [11])
    min_hired_percent: int | None = Field(None, ge=0, le=100)
    min_offers: int | None = Field(None, ge=0)
    max_offers: int | None = Field(None, ge=0)
    exclude_portfolio_required: bool = False
    min_time_left_hours: int | None = Field(None, ge=0)


class PlatformConfig(BaseModel):
    """Единая глобальная конфигурация платформы (хранится как JSON в Setting)."""

    enabled: bool = True
    max_age_hours: int | None = Field(None, ge=1, description="Override time_threshold_hours")
    min_age_seconds: int | None = Field(None, ge=0, description="Override MIN_ORDER_AGE_SECONDS")
    min_budget: int | None = Field(None, ge=0)
    max_budget: int | None = Field(None, ge=0)
    include_no_budget: bool = True  # включать заявки с "договорной"/пустым бюджетом

    # Platform-specific
    profiru: ProfiruFilters = Field(default_factory=ProfiruFilters)
    kwork: KworkFilters = Field(default_factory=KworkFilters)

    @field_validator("max_budget")
    @classmethod
    def _max_gte_min(cls, v, info):
        min_b = info.data.get("min_budget")
        if v is not None and min_b is not None and v < min_b:
            raise ValueError("max_budget must be >= min_budget")
        return v

    @classmethod
    def default_for(cls, platform: str) -> "PlatformConfig":
        """Дефолт для заданной платформы."""
        if platform not in {"profiru", "kwork"}:
            raise ValueError(f"Unknown platform: {platform}")
        return cls()
