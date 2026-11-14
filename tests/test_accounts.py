import os
import re
import unittest
import hashlib
import secrets
import tempfile
from pathlib import Path
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from concurrent.futures import ThreadPoolExecutor

from app.accounts import (
    AccountService, AccountsConfig, Actor, TestMailSink, actor, assign_owner, prune_expired,
    normalize_email, preferences, profile_scope, readable_profile, owned_profile,
    validate_multi_user_startup,
)
from app.database import make_database
from app.models import (
    AuthSession, BirthProfile, Contact, LoginToken, ProfileShare, UiSettings,
    RateLimitBucket, SecurityEvent, User, UserPreferences,
)


class AccountTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db_path = Path(directory.name) / "accounts.db"
        self.url = f"sqlite:///{self.db_path.resolve()}"
        with patch.dict(os.environ, {"HDGUI_DATABASE_URL": self.url}):
            command.upgrade(Config("alembic.ini"), "head")
        self.engine, self.sessions = make_database(self.url)
        self.addCleanup(self.engine.dispose)
        self.mailer = TestMailSink()
        self.config = AccountsConfig(
            enabled=True, public_base_url="http://localhost:8001",
            smtp_host="smtp.example.test", smtp_port=465,
            smtp_from="no-reply@example.test", test_mail_sink=True,
            allow_insecure_localhost=True,
        )
        self.service = AccountService(self.sessions, self.config, self.mailer)

    def add_user(self, email):
        key, delivery = normalize_email(email)
        from app.accounts import _now
        user = User(
            id=str(uuid4()), email_key=key, email=delivery, verified_at=_now(),
            display_name=None, status="active", created_at=_now(),
        )
        with self.sessions.begin() as db:
            db.add(user)
        return user.id

    def authenticated_actor(self, user_id):
        now = datetime.now(UTC).replace(tzinfo=None)
        session_id = str(uuid4())
        with self.sessions.begin() as db:
            user = db.get(User, user_id)
            db.add(AuthSession(
                id=session_id, session_digest=hashlib.sha256(secrets.token_urlsafe(32).encode()).hexdigest(),
                user_id=user_id, created_at=now, last_seen_at=now,
                idle_expires_at=now + timedelta(hours=12),
                expires_at=now + timedelta(days=30), revoked_at=None,
            ))
            email = user.email
        return Actor(id=user_id, session_id=session_id, email=email)

    def mail_token(self, message_index=-1):
        link = re.search(r"https?://[^ \n]+/auth/confirm\?token=([^ \n]+)",
                         self.mailer.messages[message_index][2])
        return link.group(1)

    def test_normalization_and_strict_configuration(self):
        key, delivery = normalize_email(" User@bücher.example ")
        self.assertEqual(key, "user@xn--bcher-kva.example")
        self.assertEqual(delivery, "User@xn--bcher-kva.example")
        with self.assertRaises(ValueError):
            normalize_email("not-an-email")
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            AccountsConfig(enabled=True, public_base_url="http://example.test")
        local = AccountsConfig(
            enabled=True, public_base_url="http://localhost:8001",
            smtp_host="localhost", smtp_from="test@example.test",
            allow_insecure_localhost=True,
        )
        self.assertFalse(local.secure_cookie)

    def test_confirmation_get_does_not_consume_and_post_is_single_use(self):
        app = FastAPI()
        app.state.account_service = self.service
        app.include_router(__import__("app.accounts", fromlist=["create_auth_router"]).create_auth_router(self.service))
        with TestClient(app, base_url="http://localhost:8001") as client:
            login_page = client.get("/auth/login")
            csrf = re.search(r'name="csrf" value="([^"]+)"', login_page.text).group(1)
            response = client.post(
                "/auth/login", data={"email": "Person@Example.test", "csrf": csrf},
            )
            self.assertIn("Falls die Adresse E-Mails empfangen kann", response.text)
            self.assertEqual(len(self.mailer.messages), 1)
            raw_token = self.mail_token(0)
            with self.sessions() as db:
                token_row = db.scalar(select(LoginToken))
                self.assertNotEqual(token_row.token_digest, raw_token)
                self.assertIsNone(token_row.consumed_at)
            page = client.get(f"/auth/confirm?token={raw_token}")
            self.assertIn("Das Öffnen des Links allein meldet Sie nicht an", page.text)
            self.assertIn("no-referrer", page.headers["referrer-policy"])
            with self.sessions() as db:
                self.assertIsNone(db.scalar(select(AuthSession)))
                self.assertIsNone(db.scalar(select(LoginToken)).consumed_at)
            nonce = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
            confirmed = client.post(
                "/auth/confirm", data={"token": raw_token, "csrf": nonce},
                follow_redirects=False,
            )
            self.assertEqual(confirmed.status_code, 303)
            self.assertTrue(client.cookies.get("hdgui_session"))
            current_actor = actor(_request_for(app, client), required=True)
            self.assertIsNotNone(current_actor)
            replay = client.post("/auth/confirm", data={"token": raw_token, "csrf": nonce})
            self.assertIn(replay.status_code, (400, 403))
            with self.sessions() as db:
                self.assertEqual(db.scalar(select(User)).email, "Person@example.test")
                self.assertEqual(len(list(db.scalars(select(AuthSession)))), 1)
            self.assertTrue(self.service.disable_user(current_actor.id))
            self.assertIsNone(self.service.authenticate(client.cookies.get("hdgui_session")))

    def test_authorization_preferences_and_explicit_owner_assignment(self):
        owner_id = self.add_user("owner@example.test")
        other_id = self.add_user("other@example.test")
        from app.accounts import _now
        profile = BirthProfile(
            id=str(uuid4()), name="Private", birth_local=_now(), timezone="UTC",
            birth_utc=_now(), place="Here", chart_key="x",
        )
        with self.sessions.begin() as db:
            legacy = db.get(UiSettings, 1)
            legacy.cache_seconds = 600
            legacy.autoreload = True
            legacy.center_timeline_samples = 160
            legacy.catalog_locale = "de"
            legacy.active_profile_id = profile.id
            db.add(profile)
        with self.assertRaisesRegex(RuntimeError, "explicit ownership"):
            validate_multi_user_startup(self.sessions)
        self.assertEqual(assign_owner(self.sessions, "OWNER@example.test"), 1)
        validate_multi_user_startup(self.sessions)
        owner_actor = self.authenticated_actor(owner_id)
        stranger = self.authenticated_actor(other_id)
        with self.sessions.begin() as db:
            prefs = preferences(db, owner_actor)
            self.assertEqual(prefs.active_profile_id, profile.id)
            self.assertEqual(prefs.catalog_locale, "de")
            self.assertEqual(owned_profile(db, owner_actor, profile.id).id, profile.id)
            with self.assertRaises(Exception) as caught:
                readable_profile(db, stranger, profile.id)
            self.assertEqual(getattr(caught.exception, "status_code", None), 404)
            self.assertEqual(db.scalar(select(BirthProfile).where(profile_scope(owner_actor))).id, profile.id)
        with self.sessions() as db:
            self.assertEqual(db.get(BirthProfile, profile.id).owner_user_id, owner_id)
            self.assertIsNotNone(db.get(UserPreferences, owner_id))
        with self.sessions.begin() as db:
            with self.assertRaises(HTTPException) as caught:
                preferences(db, type("Actor", (), {"id": owner_id})())
            self.assertEqual(caught.exception.status_code, 401)

    def test_shared_read_requires_both_grant_and_contact(self):
        owner_id = self.add_user("owner@example.test")
        reader_id = self.add_user("reader@example.test")
        reader_actor = self.authenticated_actor(reader_id)
        from app.accounts import _now
        profile = BirthProfile(
            id=str(uuid4()), name="Shared", birth_local=_now(), timezone="UTC",
            birth_utc=_now(), place="Here", chart_key="x", owner_user_id=owner_id,
        )
        low, high = sorted((owner_id, reader_id))
        with self.sessions.begin() as db:
            db.add_all([
                profile,
                ProfileShare(
                    id=str(uuid4()), profile_id=profile.id, recipient_user_id=reader_id,
                    granting_owner_id=owner_id, created_at=_now(),
                ),
            ])
            with self.assertRaises(Exception):
                readable_profile(db, reader_actor, profile.id)
        with self.sessions.begin() as db:
            db.add(Contact(user_low_id=low, user_high_id=high, accepted_at=_now()))
        with self.sessions() as db:
            found = readable_profile(db, reader_actor, profile.id)
            self.assertEqual(found.id, profile.id)
            with self.assertRaises(Exception):
                owned_profile(db, reader_actor, profile.id)
        self.service.disable_user(owner_id)
        with self.sessions() as db:
            with self.assertRaises(HTTPException) as caught:
                readable_profile(db, reader_actor, profile.id)
            self.assertEqual(caught.exception.status_code, 404)

    def test_persistent_rate_budget_is_atomic_and_global(self):
        self.assertTrue(self.service.check_rate_limit(
            None, "calculations", limit=3, window_seconds=60, global_limit=3,
        ))
        self.assertTrue(self.service.check_rate_limit(
            None, "calculations", limit=3, window_seconds=60, global_limit=3,
        ))
        self.assertTrue(self.service.check_rate_limit(
            None, "calculations", limit=3, window_seconds=60, global_limit=3,
        ))
        self.assertFalse(self.service.check_rate_limit(
            None, "calculations", limit=3, window_seconds=60, global_limit=3,
        ))

    def test_expired_rate_window_still_rejects_oversized_cost(self):
        with self.sessions.begin() as session:
            session.add(RateLimitBucket(
                key="account:anonymous:oversized", count=1,
                window_started_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2),
            ))
        self.assertFalse(self.service.check_rate_limit(
            None, "oversized", limit=3, window_seconds=60, cost=4,
        ))

    def test_expiry_replay_concurrent_confirmation_and_logout(self):
        self.assertTrue(self.service.request_login("expired@example.test"))
        raw_expired = self.mail_token()
        with self.sessions.begin() as db:
            token = db.scalar(select(LoginToken).where(LoginToken.token_digest == hashlib.sha256(
                raw_expired.encode(),
            ).hexdigest()))
            token.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        self.assertIsNone(self.service.confirm_token(raw_expired))

        self.assertTrue(self.service.request_login("concurrent@example.test"))
        raw = self.mail_token()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.service.confirm_token, (raw, raw)))
        successes = [result for result in results if result is not None]
        self.assertEqual(len(successes), 1)
        self.assertEqual(successes[0][0], "login")
        self.assertIsNone(self.service.confirm_token(raw))
        signed_in = self.service.authenticate(successes[0][1])
        self.assertIsNotNone(signed_in)

        self.assertTrue(self.service.request_login("concurrent@example.test"))
        another_session = self.service.confirm_token(self.mail_token())
        self.assertEqual(another_session[0], "login")
        self.service.logout(signed_in, all_sessions=True)
        self.assertIsNone(self.service.authenticate(successes[0][1]))
        self.assertIsNone(self.service.authenticate(another_session[1]))

    def test_email_change_and_account_deletion_require_fresh_links(self):
        user_id = self.add_user("old@example.test")
        owner_actor = self.authenticated_actor(user_id)
        raw_session = secrets.token_urlsafe(32)
        now = datetime.now(UTC).replace(tzinfo=None)
        with self.sessions.begin() as db:
            db.add(AuthSession(
                id=str(uuid4()), session_digest=hashlib.sha256(raw_session.encode()).hexdigest(),
                user_id=user_id, created_at=now, last_seen_at=now,
                idle_expires_at=now + timedelta(hours=12),
                expires_at=now + timedelta(days=30), revoked_at=None,
            ))
        self.assertTrue(self.service.request_login("old@example.test"))
        pending_old_login = self.mail_token()
        self.assertTrue(self.service.request_email_change(owner_actor, "New@Example.test"))
        email_change = self.service.confirm_token(self.mail_token())
        self.assertEqual(email_change, ("email_changed", None))
        self.assertIsNone(self.service.authenticate(raw_session))
        self.assertIsNone(self.service.confirm_token(pending_old_login))
        with self.sessions() as db:
            changed_user = db.get(User, user_id)
            self.assertEqual(changed_user.email, "New@example.test")
            self.assertEqual(changed_user.email_key, "new@example.test")

        recipient_id = self.add_user("recipient@example.test")
        profile_id = str(uuid4())
        with self.sessions.begin() as db:
            profile = BirthProfile(
                id=profile_id, name="Delete with owner", birth_local=now, timezone="UTC",
                birth_utc=now, place="Here", chart_key="x", owner_user_id=user_id,
            )
            db.add(profile)
            db.add(UserPreferences(
                user_id=recipient_id, current_profile_id=profile_id, locale="de",
                autoreload=False, cache_seconds=300, center_timeline_samples=64,
            ))
            db.add(SecurityEvent(
                id=str(uuid4()), actor_user_id=user_id, action="test", target_id=None,
                occurred_at=now, outcome="success",
            ))
        refreshed_actor = self.authenticated_actor(user_id)
        self.assertTrue(self.service.request_account_deletion(refreshed_actor))
        deleted = self.service.confirm_token(self.mail_token())
        self.assertEqual(deleted, ("account_deleted", None))
        with self.sessions() as db:
            self.assertIsNone(db.get(User, user_id))
            self.assertIsNone(db.get(BirthProfile, profile_id))
            self.assertIsNone(db.get(UserPreferences, recipient_id).current_profile_id)
            event = db.scalar(select(SecurityEvent).where(SecurityEvent.action == "account_deleted"))
            self.assertIsNone(event.actor_user_id)

    def test_prune_requires_explicit_cutoff_and_preserves_audit_events(self):
        user_id = self.add_user("prune@example.test")
        now = datetime.now(UTC).replace(tzinfo=None)
        with self.sessions.begin() as db:
            db.add_all([
                LoginToken(
                    id=str(uuid4()), token_digest=hashlib.sha256(secrets.token_urlsafe(32).encode()).hexdigest(),
                    email_key="prune@example.test", email_delivery="prune@example.test", purpose="login",
                    expires_at=now - timedelta(days=2), consumed_at=None, created_at=now - timedelta(days=3),
                ),
                AuthSession(
                    id=str(uuid4()), session_digest=hashlib.sha256(secrets.token_urlsafe(32).encode()).hexdigest(),
                    user_id=user_id, created_at=now - timedelta(days=3), last_seen_at=now - timedelta(days=3),
                    idle_expires_at=now - timedelta(days=2), expires_at=now - timedelta(days=2), revoked_at=None,
                ),
                SecurityEvent(
                    id=str(uuid4()), actor_user_id=user_id, action="retained", target_id=None,
                    occurred_at=now - timedelta(days=10), outcome="success",
                ),
            ])
        with self.assertRaises(ValueError):
            prune_expired(self.sessions, now)
        token_count, session_count = prune_expired(
            self.sessions, datetime.now(UTC) - timedelta(days=1),
        )
        self.assertEqual((token_count, session_count), (1, 1))
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(SecurityEvent.action)), "retained")


def _request_for(app, client):
    from starlette.requests import Request
    cookie_header = "; ".join(f"{key}={value}" for key, value in client.cookies.items())
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "https", "path": "/", "raw_path": b"/",
        "query_string": b"", "headers": [(b"cookie", cookie_header.encode())],
        "client": ("testclient", 50000), "server": ("localhost", 8001),
        "app": app,
    }
    return Request(scope)


if __name__ == "__main__":
    unittest.main()
