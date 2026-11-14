from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text,
    UniqueConstraint, text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, synonym


class Base(DeclarativeBase):
    pass


class TransitSample(Base):
    """Versioned, profile-independent ephemeris samples."""
    __tablename__ = "transit_samples"

    namespace: Mapped[str] = mapped_column(String(64), primary_key=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


class UiSettings(Base):
    __tablename__ = "ui_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cache_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    autoreload: Mapped[bool] = mapped_column(Boolean, nullable=False)
    center_timeline_samples: Mapped[int] = mapped_column(
        Integer, nullable=False, default=64, server_default="64",
    )
    catalog_locale: Mapped[str | None] = mapped_column(String(35))
    # Local single-user application: one global "current" profile, no owner yet.
    active_profile_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("birth_profiles.id", name="fk_ui_settings_active_profile", ondelete="SET NULL"),
        nullable=True,
    )


CENTER_IDS = ("Head", "Ajna", "Throat", "G", "Heart", "Spleen", "Solar", "Sacral", "Root")


class Gate(Base):
    """Canonical gate, seeded by migration 0003; texts come from a local import."""
    __tablename__ = "gates"
    __table_args__ = (
        CheckConstraint("number BETWEEN 1 AND 64", name="ck_gate_number"),
        CheckConstraint("center IN ('" + "', '".join(CENTER_IDS) + "')", name="ck_gate_center"),
    )

    number: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    center: Mapped[str] = mapped_column(String(10), nullable=False)


class Circuit(Base):
    """Canonical circuit grouping for channels."""
    __tablename__ = "circuits"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    channels: Mapped[list["Channel"]] = relationship(back_populates="circuit")


class Channel(Base):
    """Canonical channel as an unordered gate pair (gate_a < gate_b)."""
    __tablename__ = "channels"
    __table_args__ = (
        UniqueConstraint("gate_a", "gate_b", name="uq_channel_gates"),
        CheckConstraint("gate_a < gate_b", name="ck_channel_unordered"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    gate_a: Mapped[int] = mapped_column(
        Integer, ForeignKey("gates.number", name="fk_channel_gate_a", ondelete="RESTRICT"), nullable=False)
    gate_b: Mapped[int] = mapped_column(
        Integer, ForeignKey("gates.number", name="fk_channel_gate_b", ondelete="RESTRICT"), nullable=False)
    circuit_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("circuits.id", name="fk_channel_circuit", ondelete="RESTRICT"), nullable=False)
    # Nullable on purpose: the CSV has no text for some channels of multi-channel gates.
    name: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    circuit: Mapped[Circuit] = relationship(back_populates="channels")


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
    owner_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", name="fk_birth_profile_owner", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
    # Activation cache metadata, independent from the SVG cache.
    activation_key: Mapped[str | None] = mapped_column(String(64))
    activation_engine_version: Mapped[str | None] = mapped_column(String(64))
    activations_calculated_at: Mapped[datetime | None] = mapped_column(DateTime)

    activations: Mapped[list["BirthActivation"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", passive_deletes=True,
        order_by="(BirthActivation.source.desc(), BirthActivation.id)",
    )
    birth_chart: Mapped["BirthChart | None"] = relationship(
        back_populates="profile", cascade="all, delete-orphan", passive_deletes=True,
    )


class BirthActivation(Base):
    __tablename__ = "birth_activations"
    __table_args__ = (
        UniqueConstraint("profile_id", "source", "body", name="uq_birth_activation_body"),
        CheckConstraint("source IN ('personality', 'design')", name="ck_birth_activation_source"),
        CheckConstraint("gate BETWEEN 1 AND 64", name="ck_birth_activation_gate"),
        CheckConstraint("line BETWEEN 1 AND 6", name="ck_birth_activation_line"),
        CheckConstraint("longitude >= 0 AND longitude < 360", name="ck_birth_activation_longitude"),
        CheckConstraint("color BETWEEN 1 AND 6", name="ck_birth_activation_color"),
        CheckConstraint("tone BETWEEN 1 AND 6", name="ck_birth_activation_tone"),
        CheckConstraint("base BETWEEN 1 AND 5", name="ck_birth_activation_base"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("birth_profiles.id", name="fk_birth_activation_profile", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    source: Mapped[str] = mapped_column(String(12), nullable=False)
    body: Mapped[str] = mapped_column(String(40), nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    gate: Mapped[int] = mapped_column(
        Integer, ForeignKey("gates.number", name="fk_birth_activation_gate", ondelete="RESTRICT"),
        nullable=False)
    line: Mapped[int] = mapped_column(Integer, nullable=False)
    color: Mapped[int | None] = mapped_column(Integer)
    tone: Mapped[int | None] = mapped_column(Integer)
    base: Mapped[int | None] = mapped_column(Integer)
    harmonic_gate: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("gates.number", name="fk_activation_harmonic_gate", ondelete="RESTRICT"),
    )

    profile: Mapped[BirthProfile] = relationship(back_populates="activations")


class HdEntity(Base):
    """Stable concept identity/structure, independently replaceable editorial text."""
    __tablename__ = "hd_entities"
    __table_args__ = (
        CheckConstraint("description_origin IN ('unwritten', 'ai', 'human')", name="ck_hd_text_origin"),
        CheckConstraint("review_status IN ('draft', 'reviewed')", name="ck_hd_text_review"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    category: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    structure: Mapped[dict] = mapped_column(JSON, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    description_origin: Mapped[str] = mapped_column(String(12), nullable=False, default="unwritten")
    description_author: Mapped[str | None] = mapped_column(String(200))
    description_model: Mapped[str | None] = mapped_column(String(100))
    description_date: Mapped[str | None] = mapped_column(String(10))
    review_status: Mapped[str] = mapped_column(String(12), nullable=False, default="draft")


class KnowledgeEntity(Base):
    """Language-independent identity and structure shared by knowledge domains."""
    __tablename__ = "knowledge_entities"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    domain: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    structure: Mapped[dict] = mapped_column(JSON, nullable=False)


class KnowledgeEntityText(Base):
    """Localized, provenance-aware editorial content for one knowledge identity."""
    __tablename__ = "knowledge_entity_texts"
    __table_args__ = (
        CheckConstraint(
            "origin IN ('legacy', 'unwritten', 'ai', 'human')",
            name="ck_knowledge_text_origin",
        ),
        CheckConstraint(
            "review_status IN ('unknown', 'draft', 'reviewed')",
            name="ck_knowledge_text_review",
        ),
    )

    entity_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("knowledge_entities.id", ondelete="CASCADE"), primary_key=True,
    )
    locale: Mapped[str] = mapped_column(String(35), primary_key=True)
    text_kind: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False)
    origin: Mapped[str] = mapped_column(String(12), nullable=False)
    author: Mapped[str | None] = mapped_column(String(200))
    model: Mapped[str | None] = mapped_column(String(100))
    authored_at: Mapped[str | None] = mapped_column(String(10))
    review_status: Mapped[str] = mapped_column(String(12), nullable=False)


class BirthChart(Base):
    """Latest individual natal calculation, separate from SVG and editable catalog texts."""
    __tablename__ = "birth_charts"
    __table_args__ = (
        CheckConstraint("schema_version = 1", name="ck_birth_chart_schema"),
    )

    profile_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("birth_profiles.id", name="fk_birth_chart_profile", ondelete="CASCADE"),
        primary_key=True,
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    calculation_key: Mapped[str] = mapped_column(String(64), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(64), nullable=False)
    api_endpoint: Mapped[str] = mapped_column(String(40), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    birth_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    design_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    type_id: Mapped[str | None] = mapped_column(ForeignKey("hd_entities.id", ondelete="RESTRICT"))
    authority_id: Mapped[str | None] = mapped_column(ForeignKey("hd_entities.id", ondelete="RESTRICT"))
    hd_profile_id: Mapped[str] = mapped_column(ForeignKey("hd_entities.id", ondelete="RESTRICT"))
    definition_id: Mapped[str | None] = mapped_column(ForeignKey("hd_entities.id", ondelete="RESTRICT"))
    incarnation_cross_id: Mapped[str] = mapped_column(ForeignKey("hd_entities.id", ondelete="RESTRICT"))
    general: Mapped[dict] = mapped_column(JSON, nullable=False)
    variables: Mapped[dict] = mapped_column(JSON, nullable=False)
    centers: Mapped[dict] = mapped_column(JSON, nullable=False)
    channels: Mapped[list] = mapped_column(JSON, nullable=False)
    catalog_links: Mapped[dict] = mapped_column(JSON, nullable=False)
    unmapped_values: Mapped[dict] = mapped_column(JSON, nullable=False)
    raw_response: Mapped[dict] = mapped_column(JSON, nullable=False)

    profile: Mapped[BirthProfile] = relationship(back_populates="birth_chart")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="ck_user_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email_key: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    display_name: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class LoginToken(Base):
    __tablename__ = "login_tokens"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('login', 'email_change', 'account_delete')", name="ck_login_token_purpose",
        ),
        Index("ix_login_tokens_email_created", "email_key", "created_at"),
        Index("ix_login_tokens_ip_created", "requester_ip_digest", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    email_key: Mapped[str] = mapped_column(String(254), nullable=False)
    email_delivery: Mapped[str] = mapped_column(String(254), nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    requester_ip_digest: Mapped[str | None] = mapped_column(String(64))
    actor_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"),
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_user", "user_id"),
        Index("ix_auth_sessions_expiry", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class ContactRequest(Base):
    __tablename__ = "contact_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'accepted', 'declined', 'cancelled', 'expired')",
            name="ck_contact_request_status",
        ),
        CheckConstraint(
            "user_low_id IS NULL OR user_high_id IS NULL OR user_low_id < user_high_id",
            name="ck_contact_request_ordered_pair",
        ),
        Index(
            "uq_contact_request_active_pair", "user_low_id", "user_high_id",
            unique=True, sqlite_where=text("status = 'pending'"),
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "uq_contact_request_pending_email", "requester_user_id", "target_email_key",
            unique=True, sqlite_where=text("status = 'pending'"),
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    requester_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    target_email_key: Mapped[str] = mapped_column(String(254), nullable=False)
    target_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"),
    )
    user_low_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"),
    )
    user_high_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"),
    )
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        CheckConstraint("user_low_id < user_high_id", name="ck_contact_ordered_pair"),
    )

    user_low_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
    )
    user_high_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
    )
    accepted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ProfileShare(Base):
    __tablename__ = "profile_shares"
    __table_args__ = (
        UniqueConstraint("profile_id", "recipient_user_id", name="uq_profile_share_recipient"),
        CheckConstraint("granting_owner_id != recipient_user_id", name="ck_profile_share_not_owner"),
        Index("ix_profile_shares_recipient", "recipient_user_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("birth_profiles.id", ondelete="CASCADE"), nullable=False,
    )
    recipient_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    granting_owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class UserPreferences(Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
    )
    current_profile_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("birth_profiles.id", ondelete="SET NULL"),
    )
    active_profile_id = synonym("current_profile_id")
    locale: Mapped[str | None] = mapped_column(String(35))
    catalog_locale = synonym("locale")
    autoreload: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cache_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    center_timeline_samples: Mapped[int] = mapped_column(Integer, nullable=False, default=64)


class SecurityEvent(Base):
    __tablename__ = "security_events"
    __table_args__ = (
        Index("ix_security_events_actor_time", "actor_user_id", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"),
    )
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(100))
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)


class RateLimitBucket(Base):
    __tablename__ = "rate_limit_buckets"

    key: Mapped[str] = mapped_column(String(140), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
