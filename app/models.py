from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UiSettings(Base):
    __tablename__ = "ui_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cache_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    autoreload: Mapped[bool] = mapped_column(Boolean, nullable=False)


class BirthProfile(Base):
    __tablename__ = "birth_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    birth_local: Mapped[datetime] = mapped_column(DateTime)
    timezone: Mapped[str] = mapped_column(String(100))
    birth_utc: Mapped[datetime] = mapped_column(DateTime)
    place: Mapped[str] = mapped_column(String(200))
    chart_key: Mapped[str] = mapped_column(String(64))
    chart_svg: Mapped[str | None] = mapped_column(Text)
    chart_generated_at: Mapped[datetime | None] = mapped_column(DateTime)
