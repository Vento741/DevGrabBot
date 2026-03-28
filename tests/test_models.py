"""Тесты SQLAlchemy моделей."""
from src.core.models import Order, AiAnalysis, OrderAssignment, ManagerResponse, TeamMember, Setting


def test_order_model_has_required_fields():
    fields = {c.name for c in Order.__table__.columns}
    assert {"id", "external_id", "platform", "title", "description",
            "budget", "location", "deadline", "raw_text", "status",
            "created_at"}.issubset(fields)


def test_ai_analysis_model_has_required_fields():
    fields = {c.name for c in AiAnalysis.__table__.columns}
    assert {"id", "order_id", "summary", "stack", "price_min", "price_max",
            "timeline_days", "relevance_score", "complexity",
            "response_draft", "model_used", "created_at"}.issubset(fields)


def test_order_assignment_model_has_required_fields():
    fields = {c.name for c in OrderAssignment.__table__.columns}
    assert {"id", "order_id", "developer_id", "status", "price_final",
            "timeline_final", "stack_final", "custom_notes", "approved_at",
            "roadmap_text", "assigned_by", "rejection_reason"}.issubset(fields)


def test_team_member_model_has_required_fields():
    fields = {c.name for c in TeamMember.__table__.columns}
    assert {"id", "tg_id", "tg_username", "name", "role", "is_active",
            "tech_stack", "stack_priority", "bio"}.issubset(fields)


def test_manager_response_model_has_required_fields():
    fields = {c.name for c in ManagerResponse.__table__.columns}
    assert {"id", "assignment_id", "response_text", "sent_at",
            "edited_text", "sent_to_client", "sent_to_client_at"}.issubset(fields)


def test_setting_model_has_required_fields():
    fields = {c.name for c in Setting.__table__.columns}
    assert {"key", "value"}.issubset(fields)
