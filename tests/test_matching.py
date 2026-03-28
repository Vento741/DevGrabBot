"""Тесты матчинга разработчиков по стеку (src/bot/services/matching.py)."""
from types import SimpleNamespace

import pytest

from src.bot.services.matching import format_matches_block, match_developers


def make_dev(name: str, username: str, primary: list[str], secondary: list[str]) -> SimpleNamespace:
    """Фабрика тестовых объектов разработчика."""
    return SimpleNamespace(
        name=name,
        tg_username=username,
        stack_priority={"primary": primary, "secondary": secondary},
    )


# ---------------------------------------------------------------------------
# Базовые тесты матчинга
# ---------------------------------------------------------------------------

def test_match_exact_primary():
    """Все технологии заявки совпадают с primary → score 100%."""
    dev = make_dev("Денис", "web_dusha", primary=["Python", "FastAPI", "PostgreSQL"], secondary=[])
    results = match_developers(["Python", "FastAPI", "PostgreSQL"], [dev])

    assert len(results) == 1
    _, score, matched = results[0]
    assert score == 100
    assert set(matched) == {"Python", "FastAPI", "PostgreSQL"}


def test_match_mixed():
    """Часть технологий в primary, часть в secondary."""
    # Стек заявки: 3 технологии → max_possible = 6
    # primary: Python (2), FastAPI (2) = 4
    # secondary: React (1) = 1
    # raw_score = 5 / 6 * 100 = 83
    dev = make_dev("Денис", "web_dusha", primary=["Python", "FastAPI"], secondary=["React", "Vue"])
    results = match_developers(["Python", "FastAPI", "React"], [dev])

    assert len(results) == 1
    _, score, matched = results[0]
    assert score == 83
    assert set(matched) == {"Python", "FastAPI", "React"}


def test_match_no_overlap():
    """Нет совпадений → разработчик не попадает в результат."""
    dev = make_dev("Денис", "web_dusha", primary=["Java", "Spring"], secondary=["Kotlin"])
    results = match_developers(["Python", "FastAPI", "PostgreSQL"], [dev])

    assert results == []


def test_match_case_insensitive():
    """Матчинг нечувствителен к регистру: «python» == «Python»."""
    dev = make_dev("Денис", "web_dusha", primary=["python", "fastapi"], secondary=[])
    results = match_developers(["Python", "FastAPI"], [dev])

    assert len(results) == 1
    _, score, _ = results[0]
    assert score == 100


def test_match_empty_stack():
    """Пустой стек заявки → немедленный возврат пустого списка."""
    dev = make_dev("Денис", "web_dusha", primary=["Python"], secondary=["React"])
    results = match_developers([], [dev])

    assert results == []


def test_match_sorting():
    """Результат отсортирован по score по убыванию."""
    dev_high = make_dev("Денис", "web_dusha", primary=["Python", "FastAPI", "PostgreSQL"], secondary=[])
    dev_low = make_dev("Imei", "imei_rhen", primary=[], secondary=["React"])
    dev_mid = make_dev("Кирилл", "kirill_g", primary=["Python"], secondary=["React"])

    results = match_developers(["Python", "FastAPI", "React"], [dev_low, dev_high, dev_mid])

    assert len(results) == 3
    scores = [r[1] for r in results]
    assert scores == sorted(scores, reverse=True)
    # Первый — dev_high (Python + FastAPI primary)
    assert results[0][0].tg_username == "web_dusha"


def test_match_multiple_developers():
    """Корректная обработка нескольких разработчиков, возвращаются только с score > 0."""
    dev_a = make_dev("Денис", "web_dusha", primary=["Python", "FastAPI"], secondary=["React"])
    dev_b = make_dev("Imei", "imei_rhen", primary=[], secondary=["React", "Next.js"])
    dev_c = make_dev("Кирилл", "kirill_g", primary=["Java"], secondary=["Spring"])  # нет совпадений

    results = match_developers(["Python", "React"], [dev_a, dev_b, dev_c])

    usernames = [r[0].tg_username for r in results]
    assert "kirill_g" not in usernames
    assert "web_dusha" in usernames
    assert "imei_rhen" in usernames


def test_match_partial_primary():
    """Только часть технологий primary, остальные не совпадают."""
    # Стек: ["Python", "Go", "Rust"] → max_possible = 6
    # primary: Python (2) = 2
    # raw_score = 2 / 6 * 100 = 33
    dev = make_dev("Денис", "web_dusha", primary=["Python"], secondary=[])
    results = match_developers(["Python", "Go", "Rust"], [dev])

    assert len(results) == 1
    _, score, matched = results[0]
    assert score == 33
    assert matched == ["Python"]


# ---------------------------------------------------------------------------
# Тесты format_matches_block
# ---------------------------------------------------------------------------

def test_format_matches_block_empty():
    """Пустой список матчей → пустая строка."""
    assert format_matches_block([]) == ""


def test_format_matches_block_output():
    """Блок содержит имя, технологии и процент."""
    dev = make_dev("Денис", "web_dusha", primary=["Python", "FastAPI"], secondary=[])
    matches = [(dev, 90, ["Python", "FastAPI"])]
    block = format_matches_block(matches)

    assert "Подходящие разработчики" in block
    assert "@web_dusha" in block
    assert "90%" in block
    assert "Python" in block


def test_format_matches_block_no_username():
    """Если tg_username отсутствует — используется name."""
    dev = SimpleNamespace(name="Кирилл", tg_username=None, stack_priority={})
    matches = [(dev, 50, ["React"])]
    block = format_matches_block(matches)

    assert "Кирилл" in block
    assert "50%" in block
