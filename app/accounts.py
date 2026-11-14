"""Opt-in account authentication, session, and profile authorization primitives."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, case, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import make_database
from app.models import (
    AuthSession, BirthProfile, Contact, LoginToken, ProfileShare, SecurityEvent,
    RateLimitBucket, UiSettings, User, UserPreferences,
)


LOG = logging.getLogger(__name__)
COOKIE_NAME = "hdgui_session"
CSRF_COOKIE_NAME = "hdgui_csrf"
TOKEN_LIFETIME = timedelta(minutes=15)
SESSION_ABSOLUTE = timedelta(days=30)
SESSION_IDLE = timedelta(hours=12)
ISSUE_WINDOW = timedelta(hours=1)
ISSUE_LIMIT = 5
EMAIL_RE = re.compile(r"^[^@\s<>]+@[^@\s<>]+$")
LOCAL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$")
TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _write_conflict(exc: OperationalError) -> bool:
    code = getattr(exc.orig, "sqlite_errorcode", None)
    return (
        isinstance(code, int) and code & 255 in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    ) or getattr(exc.orig, "sqlstate", None) in ("40001", "40P01")


def normalize_email(raw: str) -> tuple[str, str]:
    """Return (case-insensitive lookup key, delivery spelling) for a valid address."""
    if not isinstance(raw, str):
        raise ValueError("Invalid email address.")
    email = raw.strip()
    if len(email) > 254 or not EMAIL_RE.fullmatch(email):
        raise ValueError("Invalid email address.")
    local, domain = email.rsplit("@", 1)
    if (
        not local or len(local) > 64 or not domain or not LOCAL_RE.fullmatch(local)
        or local.startswith(".") or local.endswith(".") or ".." in local
    ):
        raise ValueError("Invalid email address.")
    try:
        ascii_domain = domain.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("Invalid email address.") from exc
    labels = ascii_domain.split(".")
    if (
        not ascii_domain or len(ascii_domain) > 253
        or any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
               or not re.fullmatch(r"[a-z0-9-]+", label) for label in labels)
    ):
        raise ValueError("Invalid email address.")
    delivery = f"{local}@{ascii_domain}"
    if len(delivery) > 254:
        raise ValueError("Invalid email address.")
    return f"{local.casefold()}@{ascii_domain}", delivery


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AccountsConfig:
    enabled: bool = False
    public_base_url: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 465
    smtp_from: str | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_ssl: bool = True
    secure_cookie: bool = True
    allow_insecure_localhost: bool = False
    test_mail_sink: bool = False
    idle_minutes: int = 720
    absolute_hours: int = 720
    session_cookie_name: str = COOKIE_NAME

    def __post_init__(self):
        if not self.enabled:
            return
        if not self.public_base_url:
            raise ValueError("HDGUI_PUBLIC_BASE_URL is required when multi-user mode is enabled.")
        parsed = urlsplit(self.public_base_url)
        localhost = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" or not parsed.netloc:
            if not (self.allow_insecure_localhost and localhost and parsed.scheme == "http"):
                raise ValueError("The public base URL must use HTTPS.")
        if self.test_mail_sink and not localhost:
            raise ValueError("The test mail sink is permitted only with an explicit localhost URL.")
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError("The public base URL must not contain credentials, query, or fragment.")
        if not (1 <= self.smtp_port <= 65535):
            raise ValueError("SMTP port must be between 1 and 65535.")
        if not self.test_mail_sink:
            if not self.smtp_host or not self.smtp_from:
                raise ValueError("SMTP host and sender address are required in multi-user mode.")
            normalize_email(self.smtp_from)
        if self.idle_minutes <= 0 or self.absolute_hours <= 0:
            raise ValueError("Session expiry durations must be positive.")
        object.__setattr__(self, "secure_cookie", parsed.scheme == "https")
        object.__setattr__(self, "public_base_url", self.public_base_url.rstrip("/"))

    @classmethod
    def from_env(cls, enabled: bool | None = None) -> "AccountsConfig":
        if enabled is None:
            enabled = os.getenv("HDGUI_MULTI_USER", "").strip().lower() in {"1", "true", "yes"}
        allow_localhost = os.getenv("HDGUI_ALLOW_INSECURE_LOCALHOST") == "1"
        test_sink = os.getenv("HDGUI_MAIL_SINK") == "test"
        if not enabled:
            return cls()
        try:
            port = int(os.getenv("HDGUI_SMTP_PORT", "465"))
            idle = int(os.getenv("HDGUI_AUTH_IDLE_MINUTES", "720"))
            absolute = int(os.getenv("HDGUI_AUTH_ABSOLUTE_HOURS", "720"))
        except ValueError as exc:
            raise ValueError("Account SMTP/session environment values must be integers.") from exc
        return cls(
            enabled=True,
            public_base_url=os.getenv("HDGUI_PUBLIC_BASE_URL"),
            smtp_host=os.getenv("HDGUI_SMTP_HOST"),
            smtp_port=port,
            smtp_from=os.getenv("HDGUI_SMTP_FROM"),
            smtp_username=os.getenv("HDGUI_SMTP_USERNAME"),
            smtp_password=os.getenv("HDGUI_SMTP_PASSWORD"),
            smtp_use_ssl=os.getenv("HDGUI_SMTP_USE_SSL", "1").strip().lower() in {"1", "true", "yes"},
            allow_insecure_localhost=allow_localhost,
            test_mail_sink=test_sink,
            idle_minutes=idle,
            absolute_hours=absolute,
        )


@dataclass(frozen=True)
class Actor:
    id: str
    session_id: str
    email: str

    @property
    def user_id(self) -> str:
        return self.id


class TestMailSink:
    """Explicit test-only mail sink; messages remain in memory and never reach logs."""

    def __init__(self):
        self.messages: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, text: str) -> None:
        self.messages.append((to, subject, text))


class _RateDenied(Exception):
    pass


class AccountService:
    def __init__(self, sessions, config: AccountsConfig, mailer=None):
        self.sessions = sessions
        self.config = config
        self.mailer = mailer
        if config.enabled and config.test_mail_sink and mailer is None:
            raise ValueError("An explicit test mailer must be injected when HDGUI_MAIL_SINK=test.")

    def _send(self, to: str, subject: str, text: str) -> None:
        if self.mailer is not None:
            try:
                self.mailer.send(to, subject, text)
            except (OSError, RuntimeError, smtplib.SMTPException) as exc:
                LOG.error("Account email delivery failed (%s).", type(exc).__name__)
                raise RuntimeError("Account email delivery failed.") from None
            return
        message = EmailMessage()
        message["From"] = self.config.smtp_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text)
        try:
            if self.config.smtp_use_ssl:
                with smtplib.SMTP_SSL(
                    self.config.smtp_host, self.config.smtp_port, timeout=15,
                    context=ssl.create_default_context(),
                ) as smtp:
                    if self.config.smtp_username:
                        smtp.login(self.config.smtp_username, self.config.smtp_password or "")
                    smtp.send_message(message)
            else:
                with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=15) as smtp:
                    smtp.ehlo()
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                    if self.config.smtp_username:
                        smtp.login(self.config.smtp_username, self.config.smtp_password or "")
                    smtp.send_message(message)
        except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            # SMTP exceptions may contain addresses or message context; never include their text.
            LOG.error("Account email delivery failed (%s).", type(exc).__name__)
            raise RuntimeError("Account email delivery failed.") from None

    def send_notification(self, to: str, subject: str, text: str) -> None:
        """Send a bounded plain-text account/contact notification through configured mail."""
        _, recipient = normalize_email(to)
        self._send(recipient, subject[:180], text[:8000])

    def _security_event(self, db: Session, actor_id: str | None, action: str, target: str | None,
                        outcome: str) -> None:
        db.add(SecurityEvent(
            id=str(uuid4()), actor_user_id=actor_id, action=action[:40],
            target_id=target[:100] if target else None, occurred_at=_now(), outcome=outcome[:20],
        ))

    def _rate_allowed(self, db: Session, email_key: str, ip_digest: str | None) -> bool:
        self._consume_bucket(db, "global:email", 100, int(ISSUE_WINDOW.total_seconds()))
        self._consume_bucket(db, f"email:{_digest(email_key)}", ISSUE_LIMIT, int(ISSUE_WINDOW.total_seconds()))
        if ip_digest:
            self._consume_bucket(db, f"ip:{ip_digest}", ISSUE_LIMIT * 4, int(ISSUE_WINDOW.total_seconds()))
        return True

    @staticmethod
    def _consume_bucket(db: Session, key: str, maximum: int, window_seconds: int) -> None:
        now = _now()
        dialect = db.get_bind().dialect.name
        insert = sqlite_insert if dialect == "sqlite" else pg_insert if dialect == "postgresql" else None
        if insert:
            db.execute(insert(RateLimitBucket).values(
                key=key, window_started_at=now, count=0,
            ).on_conflict_do_nothing(index_elements=["key"]))
        else:
            row = db.scalar(select(RateLimitBucket).where(
                RateLimitBucket.key == key,
            ).with_for_update())
            if row is None:
                row = RateLimitBucket(key=key, window_started_at=now, count=0)
                db.add(row)
                db.flush()
        cutoff = now - timedelta(seconds=window_seconds)
        expired = RateLimitBucket.window_started_at <= cutoff
        result = db.execute(update(RateLimitBucket).where(
            RateLimitBucket.key == key,
            or_(expired, RateLimitBucket.count + 1 <= maximum),
        ).values(
            count=case((expired, 1), else_=RateLimitBucket.count + 1),
            window_started_at=case((expired, now), else_=RateLimitBucket.window_started_at),
        ))
        if result.rowcount != 1:
            raise _RateDenied()

    def _issue_token(self, email_key: str, email: str, purpose: str, *, actor_id=None, ip=None) -> bool:
        now = _now()
        raw_token = secrets.token_urlsafe(32)
        ip_digest = _digest(ip) if ip else None
        token = LoginToken(
            id=str(uuid4()), token_digest=_digest(raw_token), email_key=email_key,
            email_delivery=email,
            purpose=purpose, expires_at=now + TOKEN_LIFETIME, consumed_at=None,
            created_at=now, requester_ip_digest=ip_digest, actor_user_id=actor_id,
        )
        try:
            with self.sessions() as db, db.begin():
                self._rate_allowed(db, email_key, ip_digest)
                db.add(token)
                self._security_event(db, actor_id, f"{purpose}_requested", None, "accepted")
        except _RateDenied:
            return False
        except SQLAlchemyError:
            LOG.error("Account email request could not be persisted.")
            return False
        path = "/auth/confirm"
        link = f"{self.config.public_base_url}{path}?token={raw_token}"
        labels = {
            "login": ("Bei HDGUI anmelden", "Anmeldung bestätigen"),
            "email_change": ("E-Mail-Adresse für HDGUI bestätigen", "E-Mail-Änderung bestätigen"),
            "account_delete": ("HDGUI-Konto löschen", "Kontolöschung bestätigen"),
        }
        subject, heading = labels[purpose]
        body = (
            f"{heading}:\n\n{link}\n\nDieser Link läuft in 15 Minuten ab. "
            "Falls Sie ihn nicht angefordert haben, ignorieren Sie diese Nachricht."
        )
        try:
            self._send(email, subject, body)
        except RuntimeError:
            with self.sessions() as db, db.begin():
                db.execute(update(LoginToken).where(LoginToken.id == token.id).values(consumed_at=_now()))
                self._security_event(db, actor_id, f"{purpose}_delivery", None, "failed")
            return False
        return True

    def request_login(self, email: str, ip: str | None = None) -> bool:
        try:
            key, delivery = normalize_email(email)
        except ValueError:
            return False
        return self._issue_token(key, delivery, "login", ip=ip)

    def request_email_change(self, actor: Actor, new_email: str, ip: str | None = None) -> bool:
        if not self._actor_is_current(actor):
            return False
        try:
            key, delivery = normalize_email(new_email)
        except ValueError:
            return False
        with self.sessions() as db:
            if db.scalar(select(User.id).where(User.email_key == key, User.id != actor.id)):
                return False
            user = db.get(User, actor.id)
            if not user:
                return False
        return self._issue_token(key, delivery, "email_change", actor_id=actor.id, ip=ip)

    def request_account_deletion(self, actor: Actor, ip: str | None = None) -> bool:
        if not self._actor_is_current(actor):
            return False
        with self.sessions() as db:
            user = db.get(User, actor.id)
            if not user or not user.verified_at:
                return False
            key, delivery = user.email_key, user.email
        return self._issue_token(key, delivery, "account_delete", actor_id=actor.id, ip=ip)

    def _actor_is_current(self, actor: Actor) -> bool:
        now = _now()
        with self.sessions() as db:
            row = db.scalar(select(AuthSession).where(
                AuthSession.id == actor.session_id,
                AuthSession.user_id == actor.id,
                AuthSession.revoked_at.is_(None),
                AuthSession.idle_expires_at > now,
                AuthSession.expires_at > now,
            ))
            user = db.get(User, actor.id)
            return row is not None and user is not None and user.status == "active"

    def _new_session(self, db: Session, user: User, now: datetime) -> str:
        raw = secrets.token_urlsafe(32)
        db.add(AuthSession(
            id=str(uuid4()), session_digest=_digest(raw), user_id=user.id,
            created_at=now, last_seen_at=now,
            idle_expires_at=min(now + timedelta(minutes=self.config.idle_minutes),
                                now + timedelta(hours=self.config.absolute_hours)),
            expires_at=now + timedelta(hours=self.config.absolute_hours),
            revoked_at=None,
        ))
        return raw

    def confirm_token(self, raw_token: str) -> tuple[str, str | None] | None:
        try:
            return self._confirm_token_transaction(raw_token)
        except OperationalError as exc:
            if not _write_conflict(exc):
                raise
            # A simultaneous confirmation may lose the atomic SQLite write race.
            LOG.warning("Account confirmation interrupted by a database write conflict.")
            return None

    def _confirm_token_transaction(self, raw_token: str) -> tuple[str, str | None] | None:
        if not raw_token or len(raw_token) > 256:
            return None
        now = _now()
        with self.sessions() as db, db.begin():
            token = db.scalar(select(LoginToken).where(
                LoginToken.token_digest == _digest(raw_token),
                LoginToken.consumed_at.is_(None),
                LoginToken.expires_at > now,
            ))
            if token is None:
                return None
            changed = db.execute(update(LoginToken).where(
                LoginToken.id == token.id,
                LoginToken.consumed_at.is_(None),
                LoginToken.expires_at > now,
            ).values(consumed_at=now)).rowcount
            if changed != 1:
                return None
            if token.purpose == "login":
                user = db.scalar(select(User).where(User.email_key == token.email_key))
                if user is None:
                    user = User(
                        id=str(uuid4()), email_key=token.email_key, email=token.email_delivery,
                        verified_at=now, display_name=None, status="active", created_at=now,
                    )
                    db.add(user)
                    db.flush()
                elif user.status != "active":
                    self._security_event(db, user.id, "login", None, "disabled")
                    return None
                else:
                    user.verified_at = user.verified_at or now
                raw_session = self._new_session(db, user, now)
                self._security_event(db, user.id, "login", None, "success")
                return ("login", raw_session)
            user = db.get(User, token.actor_user_id) if token.actor_user_id else None
            if user is None or user.status != "active" or not user.verified_at:
                return None
            if token.purpose == "email_change":
                other = db.scalar(select(User.id).where(
                    User.email_key == token.email_key, User.id != user.id,
                ))
                if other:
                    return None
                old_email_key = user.email_key
                user.email_key = token.email_key
                user.email = token.email_delivery
                user.verified_at = now
                db.execute(update(LoginToken).where(
                    or_(
                        LoginToken.actor_user_id == user.id,
                        LoginToken.email_key.in_((old_email_key, token.email_key)),
                    ),
                    LoginToken.consumed_at.is_(None),
                ).values(consumed_at=now))
                db.execute(update(AuthSession).where(
                    AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None),
                ).values(revoked_at=now))
                self._security_event(db, user.id, "email_changed", None, "success")
                return ("email_changed", None)
            if token.purpose == "account_delete":
                self._security_event(db, user.id, "account_deleted", None, "success")
                # Remove owned records explicitly; the FK intentionally prevents silent transfer.
                db.execute(delete(BirthProfile).where(BirthProfile.owner_user_id == user.id))
                db.delete(user)
                return ("account_deleted", None)
        return None

    def authenticate(self, cookie: str | None) -> Actor | None:
        if not cookie or len(cookie) > 256:
            return None
        now = _now()
        with self.sessions() as db, db.begin():
            row = db.scalar(select(AuthSession).where(
                AuthSession.session_digest == _digest(cookie),
                AuthSession.revoked_at.is_(None),
                AuthSession.idle_expires_at > now,
                AuthSession.expires_at > now,
            ))
            if row is None:
                return None
            user = db.get(User, row.user_id)
            if user is None or user.status != "active":
                return None
            row.last_seen_at = now
            row.idle_expires_at = min(
                now + timedelta(minutes=self.config.idle_minutes), row.expires_at,
            )
            return Actor(id=user.id, session_id=row.id, email=user.email)

    def logout(self, actor: Actor, all_sessions: bool = False) -> None:
        if not self._actor_is_current(actor):
            return
        now = _now()
        with self.sessions() as db, db.begin():
            where = AuthSession.user_id == actor.id if all_sessions else AuthSession.id == actor.session_id
            db.execute(update(AuthSession).where(
                where, AuthSession.revoked_at.is_(None),
            ).values(revoked_at=now))
            self._security_event(db, actor.id, "logout_all" if all_sessions else "logout",
                                 None, "success")

    def disable_user(self, user_id: str) -> bool:
        """Disable an account and revoke its sessions and outstanding verification links."""
        now = _now()
        with self.sessions() as db, db.begin():
            user = db.get(User, user_id)
            if user is None:
                return False
            user.status = "disabled"
            db.execute(update(AuthSession).where(
                AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None),
            ).values(revoked_at=now))
            db.execute(update(LoginToken).where(
                LoginToken.actor_user_id == user_id,
                LoginToken.consumed_at.is_(None),
            ).values(consumed_at=now))
            self._security_event(db, user_id, "account_disabled", None, "success")
            return True

    def check_rate_limit(
        self, actor: Actor | None, bucket: str, limit: int, window_seconds: int,
        *, cost: int = 1, global_limit: int | None = None,
    ) -> bool:
        """Persistently consume a per-account and optional global request budget."""
        if limit < 1 or window_seconds < 1 or cost < 1 or (global_limit is not None and global_limit < 1):
            raise ValueError("Rate-limit values must be positive.")
        if actor is not None and not self._actor_is_current(actor):
            return False
        account_key = f"account:{actor.id if actor else 'anonymous'}:{bucket[:40]}"
        keys = [(account_key, limit)]
        if global_limit is not None:
            keys.append((f"global:{bucket[:40]}", global_limit))

        for attempt in range(3):
            now = _now()
            try:
                with self.sessions() as db, db.begin():
                    for key, maximum in keys:
                        if cost > maximum:
                            raise _RateDenied()
                        dialect = db.get_bind().dialect.name
                        insert = sqlite_insert if dialect == "sqlite" else pg_insert if dialect == "postgresql" else None
                        if insert:
                            db.execute(insert(RateLimitBucket).values(
                                key=key, window_started_at=now, count=0,
                            ).on_conflict_do_nothing(index_elements=["key"]))
                        cutoff = now - timedelta(seconds=window_seconds)
                        expired = RateLimitBucket.window_started_at <= cutoff
                        charged = case(
                            (expired, cost),
                            else_=RateLimitBucket.count + cost,
                        )
                        result = db.execute(update(RateLimitBucket).where(
                            RateLimitBucket.key == key,
                            or_(expired, RateLimitBucket.count + cost <= maximum),
                        ).values(
                            count=charged,
                            window_started_at=case(
                                (expired, now), else_=RateLimitBucket.window_started_at,
                            ),
                        ))
                        if result.rowcount != 1:
                            raise _RateDenied()
                return True
            except _RateDenied:
                return False
            except OperationalError as exc:
                if not _write_conflict(exc):
                    raise
                if attempt == 2:
                    LOG.warning("Account request budget blocked by a database write conflict.")
                    return False
                time.sleep(0.01 * (attempt + 1))
        return False


def actor(request: Request, required: bool = True) -> Actor | None:
    """Resolve the request's opaque server-side session into its current actor."""
    service: AccountService | None = getattr(request.app.state, "account_service", None)
    if not service or not service.config.enabled:
        if required:
            raise HTTPException(401, "Authentication required.")
        return None
    resolved = service.authenticate(request.cookies.get(service.config.session_cookie_name))
    if resolved is None and required:
        raise HTTPException(401, "Authentication required.")
    return resolved


def csrf_token(response: Response, config: AccountsConfig) -> str:
    token = secrets.token_urlsafe(32)
    response.set_cookie(
        CSRF_COOKIE_NAME, token, secure=config.secure_cookie, httponly=False,
        samesite="lax", path="/", max_age=SESSION_ABSOLUTE.total_seconds(),
    )
    return token


def csrf_valid(request: Request, submitted: str | None) -> bool:
    expected = request.cookies.get(CSRF_COOKIE_NAME)
    return bool(expected and submitted and hmac.compare_digest(expected, submitted))


def _same_profile_scope(actor_value: Actor, include_shared: bool):
    owner = BirthProfile.owner_user_id == actor_value.id
    if not include_shared:
        return owner
    contact_exists = select(Contact.user_low_id).where(
        or_(
            and_(
                Contact.user_low_id == ProfileShare.granting_owner_id,
                Contact.user_high_id == ProfileShare.recipient_user_id,
            ),
            and_(
                Contact.user_low_id == ProfileShare.recipient_user_id,
                Contact.user_high_id == ProfileShare.granting_owner_id,
            ),
        ),
    ).exists()
    share_exists = select(ProfileShare.id).join(
        User, User.id == ProfileShare.granting_owner_id,
    ).where(
        User.status == "active",
        ProfileShare.profile_id == BirthProfile.id,
        ProfileShare.recipient_user_id == actor_value.id,
        ProfileShare.granting_owner_id == BirthProfile.owner_user_id,
        contact_exists,
    ).exists()
    return or_(owner, share_exists)


def profile_scope(actor_value: Actor, shared: bool = False):
    """SQL predicate for owned profiles, optionally including active shared profiles."""
    return _same_profile_scope(actor_value, shared)


def owned_profile(db_session: Session, actor_value: Actor, profile_id: str) -> BirthProfile:
    validate_actor(db_session, actor_value)
    profile = db_session.scalar(select(BirthProfile).where(
        BirthProfile.id == profile_id, profile_scope(actor_value),
    ))
    if profile is None:
        raise HTTPException(404, "Profile not found.")
    return profile


def readable_profile(db_session: Session, actor_value: Actor, profile_id: str) -> BirthProfile:
    validate_actor(db_session, actor_value)
    profile = db_session.scalar(select(BirthProfile).where(
        BirthProfile.id == profile_id, profile_scope(actor_value, shared=True),
    ))
    if profile is None:
        raise HTTPException(404, "Profile not found.")
    return profile


def validate_actor(db_session: Session, actor_value: Actor) -> None:
    """Revalidate a request actor before any database-backed protected operation."""
    session_id = getattr(actor_value, "session_id", None)
    if not session_id:
        raise HTTPException(401, "Anmeldung erforderlich.")
    now = _now()
    live = db_session.scalar(select(AuthSession.id).join(
        User, User.id == AuthSession.user_id,
    ).where(
        AuthSession.id == session_id,
        AuthSession.user_id == actor_value.id,
        AuthSession.revoked_at.is_(None),
        AuthSession.idle_expires_at > now,
        AuthSession.expires_at > now,
        User.status == "active",
    ))
    if live is None:
        raise HTTPException(401, "Anmeldung erforderlich.")


def preferences(db_session: Session, actor_value: Actor) -> UserPreferences:
    validate_actor(db_session, actor_value)
    prefs = db_session.get(UserPreferences, actor_value.id)
    if prefs is None:
        prefs = UserPreferences(
            user_id=actor_value.id, current_profile_id=None, locale=None,
            autoreload=False, cache_seconds=300, center_timeline_samples=64,
        )
        db_session.add(prefs)
        db_session.flush()
    return prefs


def validate_multi_user_startup(sessions) -> None:
    """Reject protected multi-user startup until legacy profiles are explicitly owned."""
    with sessions() as db:
        if db.scalar(select(BirthProfile.id).where(BirthProfile.owner_user_id.is_(None)).limit(1)):
            raise RuntimeError(
                "Multi-user mode requires explicit ownership assignment for every legacy profile. "
                "Run `python -m app.accounts --assign-owner VERIFIED_EMAIL`."
            )


def assign_owner(sessions, email: str) -> int:
    """Explicitly assign only unowned legacy profiles and copy singleton UI settings."""
    email_key, _delivery = normalize_email(email)
    with sessions() as db, db.begin():
        user = db.scalar(select(User).where(User.email_key == email_key))
        if user is None or user.verified_at is None or user.status != "active":
            raise ValueError("Owner must be an existing active, verified account.")
        legacy = db.get(UiSettings, 1)
        profile_ids = list(db.scalars(select(BirthProfile.id).where(
            BirthProfile.owner_user_id.is_(None),
        )))
        db.execute(update(BirthProfile).where(
            BirthProfile.owner_user_id.is_(None),
        ).values(owner_user_id=user.id))
        if db.get(UserPreferences, user.id) is None:
            current_profile_id = legacy.active_profile_id if legacy else None
            if current_profile_id and current_profile_id not in profile_ids:
                current_profile_id = None
            db.add(UserPreferences(
                user_id=user.id, current_profile_id=current_profile_id,
                locale=legacy.catalog_locale if legacy else None,
                autoreload=legacy.autoreload if legacy else False,
                cache_seconds=legacy.cache_seconds if legacy else 300,
                center_timeline_samples=legacy.center_timeline_samples if legacy else 64,
            ))
        return len(profile_ids)


def prune_expired(sessions, cutoff: datetime) -> tuple[int, int]:
    """Prune old expired tokens and sessions; deliberately retain security-event history."""
    if cutoff.tzinfo is None:
        raise ValueError("Pruning cutoff must include a timezone.")
    cutoff_utc = cutoff.astimezone(UTC).replace(tzinfo=None)
    with sessions() as db, db.begin():
        token_count = db.execute(delete(LoginToken).where(
            LoginToken.expires_at < cutoff_utc,
        )).rowcount or 0
        session_count = db.execute(delete(AuthSession).where(
            or_(
                AuthSession.expires_at < cutoff_utc,
                AuthSession.revoked_at < cutoff_utc,
            ),
        )).rowcount or 0
        return token_count, session_count


def create_auth_router(service: AccountService) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["authentication"])

    @router.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        csrf = getattr(request.state, "csrf", None) or secrets.token_urlsafe(32)
        response = TEMPLATES.TemplateResponse(
            request=request, name="auth_login.html",
            context={"csrf": csrf},
        )
        response.set_cookie(
            CSRF_COOKIE_NAME, csrf, secure=service.config.secure_cookie,
            httponly=False, samesite="lax", path="/",
            max_age=int(SESSION_ABSOLUTE.total_seconds()),
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @router.post("/login")
    def login(request: Request, email: str = Form(...), csrf: str = Form("")):
        if not csrf_valid(request, csrf):
            raise HTTPException(403, "Invalid CSRF token.")
        service.request_login(email, request.client.host if request.client else None)
        response = TEMPLATES.TemplateResponse(
            request=request, name="auth_message.html",
            context={"message": "Falls die Adresse E-Mails empfangen kann, wurde ein Anmeldelink gesendet."},
            status_code=200,
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @router.get("/confirm", response_class=HTMLResponse)
    def confirm_page(request: Request, token: str = ""):
        if not token or len(token) > 256:
            raise HTTPException(400, "Der Link ist ungültig oder abgelaufen.")
        nonce = secrets.token_urlsafe(32)
        response = TEMPLATES.TemplateResponse(
            request=request, name="auth_confirm.html",
            context={"csrf": nonce, "token": token},
        )
        response.set_cookie(
            "hdgui_login_nonce", nonce, secure=service.config.secure_cookie,
            httponly=True, samesite="lax", path="/auth/confirm", max_age=600,
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @router.post("/confirm")
    def confirm(
        request: Request,
        token: str = Form(...),
        csrf: str = Form(""),
    ):
        nonce = request.cookies.get("hdgui_login_nonce")
        if not nonce or not csrf or not hmac.compare_digest(nonce, csrf):
            raise HTTPException(403, "Bestätigung ungültig.")
        result = service.confirm_token(token)
        response: Response
        if result is None:
            response = TEMPLATES.TemplateResponse(
                request=request, name="auth_message.html",
                context={"message": "Dieser Link ist ungültig, abgelaufen oder bereits verwendet."},
                status_code=400,
            )
        elif result[0] == "login":
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(
                service.config.session_cookie_name, result[1],
                secure=service.config.secure_cookie, httponly=True,
                samesite="lax", path="/", max_age=int(SESSION_ABSOLUTE.total_seconds()),
            )
        else:
            messages = {
                "email_changed": "Ihre E-Mail-Adresse wurde bestätigt und geändert.",
                "account_deleted": "Ihr Konto wurde gelöscht.",
            }
            response = TEMPLATES.TemplateResponse(
                request=request, name="auth_message.html",
                context={"message": messages[result[0]]},
            )
            response.delete_cookie(service.config.session_cookie_name, path="/")
        response.delete_cookie("hdgui_login_nonce", path="/auth/confirm")
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @router.post("/logout")
    def logout(
        request: Request,
        csrf: str = Form(""),
        all_sessions: bool = Form(False),
    ):
        if not csrf_valid(request, csrf):
            raise HTTPException(403, "Invalid CSRF token.")
        current = actor(request, required=True)
        service.logout(current, all_sessions=all_sessions)
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(service.config.session_cookie_name, path="/")
        return response

    @router.post("/email-change")
    def email_change(request: Request, email: str = Form(...), csrf: str = Form("")):
        if not csrf_valid(request, csrf):
            raise HTTPException(403, "Invalid CSRF token.")
        current = actor(request, required=True)
        service.request_email_change(current, email, request.client.host if request.client else None)
        response = TEMPLATES.TemplateResponse(
            request=request, name="auth_message.html",
            context={"message": "Falls die Adresse verfügbar ist, wurde ein Bestätigungslink gesendet."},
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.post("/delete-account")
    def delete_account(request: Request, csrf: str = Form("")):
        if not csrf_valid(request, csrf):
            raise HTTPException(403, "Invalid CSRF token.")
        current = actor(request, required=True)
        service.request_account_deletion(current, request.client.host if request.client else None)
        response = TEMPLATES.TemplateResponse(
            request=request, name="auth_message.html",
            context={"message": "Ein Bestätigungslink wurde an Ihre verifizierte Adresse gesendet."},
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    return router


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Account maintenance and explicit legacy ownership.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--assign-owner", metavar="VERIFIED_EMAIL")
    action.add_argument("--invite-owner", metavar="EMAIL")
    action.add_argument("--prune-before", metavar="ISO_8601_CUTOFF")
    args = parser.parse_args(argv)
    if args.prune_before:
        try:
            cutoff = datetime.fromisoformat(args.prune_before.replace("Z", "+00:00"))
            if cutoff.tzinfo is None:
                raise ValueError("Cutoff must include a timezone, e.g. 2026-01-01T00:00:00Z.")
        except ValueError as exc:
            parser.error(str(exc))
        settings = Settings()
        engine, sessions = make_database(settings.database_url)
        try:
            token_count, session_count = prune_expired(sessions, cutoff)
            print(f"Pruned {token_count} expired token(s) and {session_count} expired/revoked session(s).")
        finally:
            engine.dispose()
        return 0
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    settings = Settings()
    engine, sessions = make_database(settings.database_url)
    try:
        if args.invite_owner:
            accounts_config = AccountsConfig.from_env(enabled=True)
            service = AccountService(sessions, accounts_config)
            if not service.request_login(args.invite_owner):
                parser.error("Owner setup invitation was not queued or was rate-limited.")
            print("If the address can receive mail, an owner setup link has been sent.")
        else:
            count = assign_owner(sessions, args.assign_owner)
            print(f"Assigned {count} previously unowned profile(s).")
    except (ValueError, IntegrityError) as exc:
        parser.error(str(exc))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
