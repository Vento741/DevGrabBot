"""SQLAlchemy модели данных."""
import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, JSON, String, Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class OrderStatus(str, enum.Enum):
    new = "new"
    analyzing = "analyzing"
    reviewed = "reviewed"
    assigned = "assigned"
    completed = "completed"
    skipped = "skipped"


class AssignmentStatus(str, enum.Enum):
    pending = "pending"
    editing = "editing"
    approved = "approved"
    sent = "sent"
    in_progress = "in_progress"
    cancelled = "cancelled"
    rejected = "rejected"
    reassigned = "reassigned"


class TeamRole(str, enum.Enum):
    developer = "developer"
    manager = "manager"


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(50), unique=True)
    platform: Mapped[str] = mapped_column(String(50), default="profiru")
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    budget: Mapped[str | None] = mapped_column(String(200), nullable=True)
    response_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    materials: Mapped[list | None] = mapped_column(JSON, nullable=True)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    deadline: Mapped[str | None] = mapped_column(String(200), nullable=True)
    raw_text: Mapped[str] = mapped_column(Text)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus), default=OrderStatus.new,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(),
    )

    analyses: Mapped[list["AiAnalysis"]] = relationship(back_populates="order")
    assignments: Mapped[list["OrderAssignment"]] = relationship(back_populates="order")
    notifications: Mapped[list["OrderNotification"]] = relationship(
        back_populates="order", cascade="all, delete-orphan",
    )


class AiAnalysis(Base):
    __tablename__ = "ai_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    summary: Mapped[str] = mapped_column(Text)
    stack: Mapped[list] = mapped_column(JSON, default=list)
    price_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timeline_days: Mapped[str | None] = mapped_column(String(50), nullable=True)
    relevance_score: Mapped[int] = mapped_column(Integer, default=0)
    complexity: Mapped[str] = mapped_column(String(20), default="medium")
    response_draft: Mapped[str] = mapped_column(Text, default="")
    model_used: Mapped[str] = mapped_column(String(100))
    extra_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(),
    )

    order: Mapped["Order"] = relationship(back_populates="analyses")


class OrderAssignment(Base):
    __tablename__ = "order_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    developer_id: Mapped[int] = mapped_column(ForeignKey("team_members.id"))
    status: Mapped[AssignmentStatus] = mapped_column(
        Enum(AssignmentStatus), default=AssignmentStatus.pending,
    )
    price_final: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timeline_final: Mapped[str | None] = mapped_column(String(100), nullable=True)
    stack_final: Mapped[list | None] = mapped_column(JSON, nullable=True)
    custom_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    taken_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    roadmap_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("team_members.id"), nullable=True,
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    group_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    in_progress_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    order: Mapped["Order"] = relationship(back_populates="assignments")
    developer: Mapped["TeamMember"] = relationship(
        back_populates="assignments", foreign_keys="[OrderAssignment.developer_id]",
    )
    manager_response: Mapped["ManagerResponse | None"] = relationship(
        back_populates="assignment", uselist=False,
    )


class OrderNotification(Base):
    """Трекинг уведомлений о заказах, отправленных dev'ам в личку."""
    __tablename__ = "order_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    developer_id: Mapped[int] = mapped_column(ForeignKey("team_members.id"))
    message_id: Mapped[int] = mapped_column(BigInteger)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(),
    )

    order: Mapped["Order"] = relationship(back_populates="notifications")
    developer: Mapped["TeamMember"] = relationship()


class ManagerResponse(Base):
    __tablename__ = "manager_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("order_assignments.id"))
    response_text: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(),
    )
    edited_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_to_client: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_to_client_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    assignment: Mapped["OrderAssignment"] = relationship(back_populates="manager_response")


class TeamMember(Base):
    __tablename__ = "team_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    tg_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[TeamRole] = mapped_column(Enum(TeamRole))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    tech_stack: Mapped[list] = mapped_column(JSON, default=list)
    stack_priority: Mapped[dict] = mapped_column(JSON, default=dict)
    bio: Mapped[str] = mapped_column(Text, default="")
    notify_assignments: Mapped[bool] = mapped_column(Boolean, default=True)

    assignments: Mapped[list["OrderAssignment"]] = relationship(
        back_populates="developer", foreign_keys="[OrderAssignment.developer_id]",
    )


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
