"""Add account, authentication, contact, sharing and preference foundations."""
from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email_key", sa.String(254), nullable=False, unique=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("verified_at", sa.DateTime()),
        sa.Column("display_name", sa.String(120)),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_user_status"),
    )
    owner_column = sa.Column(
        "owner_user_id", sa.String(36),
        sa.ForeignKey("users.id", name="fk_birth_profile_owner", ondelete="RESTRICT"),
        nullable=True,
    )
    if op.get_bind().dialect.name == "sqlite":
        # SQLite permits adding this nullable REFERENCES column directly. Raw DDL avoids
        # Alembic's unsupported ALTER-constraint path and preserves dependent profile FKs.
        op.execute(
            "ALTER TABLE birth_profiles ADD COLUMN owner_user_id VARCHAR(36) "
            "CONSTRAINT fk_birth_profile_owner REFERENCES users(id) ON DELETE RESTRICT"
        )
    else:
        op.add_column("birth_profiles", owner_column)
    op.create_index("ix_birth_profiles_owner_user_id", "birth_profiles", ["owner_user_id"])

    op.create_table(
        "login_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("email_key", sa.String(254), nullable=False),
        sa.Column("email_delivery", sa.String(254), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("requester_ip_digest", sa.String(64)),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.CheckConstraint(
            "purpose IN ('login', 'email_change', 'account_delete')", name="ck_login_token_purpose",
        ),
    )
    op.create_index("ix_login_tokens_email_created", "login_tokens", ["email_key", "created_at"])
    op.create_index("ix_login_tokens_ip_created", "login_tokens", ["requester_ip_digest", "created_at"])

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime()),
    )
    op.create_index("ix_auth_sessions_user", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expiry", "auth_sessions", ["expires_at"])

    op.create_table(
        "contact_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("requester_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_email_key", sa.String(254), nullable=False),
        sa.Column("target_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("user_low_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("user_high_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'declined', 'cancelled', 'expired')",
            name="ck_contact_request_status",
        ),
        sa.CheckConstraint(
            "user_low_id IS NULL OR user_high_id IS NULL OR user_low_id < user_high_id",
            name="ck_contact_request_ordered_pair",
        ),
    )
    op.create_index(
        "uq_contact_request_active_pair", "contact_requests", ["user_low_id", "user_high_id"],
        unique=True, sqlite_where=sa.text("status = 'pending'"),
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "uq_contact_request_pending_email", "contact_requests",
        ["requester_user_id", "target_email_key"], unique=True,
        sqlite_where=sa.text("status = 'pending'"),
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "contacts",
        sa.Column("user_low_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_high_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("accepted_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("user_low_id < user_high_id", name="ck_contact_ordered_pair"),
    )
    op.create_table(
        "profile_shares",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("profile_id", sa.String(36), sa.ForeignKey("birth_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recipient_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("granting_owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("profile_id", "recipient_user_id", name="uq_profile_share_recipient"),
        sa.CheckConstraint("granting_owner_id != recipient_user_id", name="ck_profile_share_not_owner"),
    )
    op.create_index("ix_profile_shares_recipient", "profile_shares", ["recipient_user_id"])

    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("current_profile_id", sa.String(36), sa.ForeignKey("birth_profiles.id", ondelete="SET NULL")),
        sa.Column("locale", sa.String(35)),
        sa.Column("autoreload", sa.Boolean(), nullable=False),
        sa.Column("cache_seconds", sa.Integer(), nullable=False),
        sa.Column("center_timeline_samples", sa.Integer(), nullable=False),
    )
    op.create_table(
        "security_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("target_id", sa.String(100)),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
    )
    op.create_index("ix_security_events_actor_time", "security_events", ["actor_user_id", "occurred_at"])
    op.create_table(
        "rate_limit_buckets",
        sa.Column("key", sa.String(140), primary_key=True),
        sa.Column("window_started_at", sa.DateTime(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
    )


def downgrade():
    op.drop_table("rate_limit_buckets")
    op.drop_index("ix_security_events_actor_time", table_name="security_events")
    op.drop_table("security_events")
    op.drop_table("user_preferences")
    op.drop_index("ix_profile_shares_recipient", table_name="profile_shares")
    op.drop_table("profile_shares")
    op.drop_table("contacts")
    op.drop_index("uq_contact_request_pending_email", table_name="contact_requests")
    op.drop_index("uq_contact_request_active_pair", table_name="contact_requests")
    op.drop_table("contact_requests")
    op.drop_index("ix_auth_sessions_expiry", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("ix_login_tokens_ip_created", table_name="login_tokens")
    op.drop_index("ix_login_tokens_email_created", table_name="login_tokens")
    op.drop_table("login_tokens")
    op.drop_index("ix_birth_profiles_owner_user_id", table_name="birth_profiles")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("birth_profiles", recreate="always") as batch:
            batch.drop_column("owner_user_id")
    else:
        op.drop_column("birth_profiles", "owner_user_id")
    op.drop_table("users")
