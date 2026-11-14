"""Accepted contacts and explicitly revocable, read-only profile grants."""
from datetime import UTC, datetime, timedelta
from contextlib import contextmanager
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.accounts import normalize_email, owned_profile, validate_actor
from app.models import Contact, ContactRequest, ProfileShare, SecurityEvent, User, UserPreferences


def now():
    return datetime.now(UTC).replace(tzinfo=None)


def pair(first, second):
    return tuple(sorted((first, second)))


def audit(session, actor_id, action, target):
    session.add(SecurityEvent(id=str(uuid4()), actor_user_id=actor_id, action=action,
                              target_id=target, occurred_at=now(), outcome="success"))


def revoke(session, profile_id, recipient):
    session.execute(delete(ProfileShare).where(
        ProfileShare.profile_id == profile_id, ProfileShare.recipient_user_id == recipient,
    ))
    session.execute(update(UserPreferences).where(
        UserPreferences.user_id == recipient, UserPreferences.current_profile_id == profile_id,
    ).values(current_profile_id=None))


@contextmanager
def transaction(sessions, current):
    with sessions.begin() as session:
        if session.bind.dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        validate_actor(session, current)
        yield session


def sharing_router(accounts, templates, actor, token, check_csrf):
    router = APIRouter()
    sessions = accounts.sessions

    def contacts_for(session, user_id, page=1):
        contact_exists = select(Contact.user_low_id).where(or_(
            (Contact.user_low_id == user_id) & (Contact.user_high_id == User.id),
            (Contact.user_high_id == user_id) & (Contact.user_low_id == User.id),
        )).exists()
        return session.scalars(select(User).where(contact_exists, User.status == "active")
                               .order_by(User.email).offset((page - 1) * 25).limit(25)).all()

    @router.get("/contacts")
    def contacts(request: Request, page: int = 1):
        if page < 1:
            raise HTTPException(422, "Ungültige Seite.")
        current = actor(request)
        email_key, _ = normalize_email(current.email)
        with transaction(sessions, current) as session:
            session.execute(update(ContactRequest).where(
                ContactRequest.status == "pending", ContactRequest.expires_at <= now(),
            ).values(status="expired"))
            incoming = session.scalars(select(ContactRequest).where(
                ContactRequest.target_email_key == email_key, ContactRequest.status == "pending",
            ).order_by(ContactRequest.created_at).offset((page - 1) * 25).limit(25)).all()
            outgoing = session.scalars(select(ContactRequest).where(
                ContactRequest.requester_user_id == current.id, ContactRequest.status == "pending",
            ).order_by(ContactRequest.created_at).offset((page - 1) * 25).limit(25)).all()
            users = contacts_for(session, current.id, page)
            names = {row.id: row.email for row in session.scalars(select(User).where(
                User.id.in_([item.requester_user_id for item in incoming]),
            ))}
        return templates.TemplateResponse(request=request, name="contacts.html", context={
            "contacts": users, "incoming": incoming,
            "outgoing": outgoing, "names": names, "csrf": token(request), "page": page,
        })

    @router.post("/contacts")
    async def request_contact(request: Request):
        form = await request.form()
        check_csrf(request, form)
        current = actor(request)
        try:
            email_key, delivery = normalize_email(str(form.get("email", "")))
        except ValueError as exc:
            raise HTTPException(422, "Bitte eine gültige E-Mail-Adresse angeben.") from exc
        if email_key == normalize_email(current.email)[0]:
            raise HTTPException(422, "Die eigene Adresse kann kein Kontakt sein.")
        if not accounts.check_rate_limit(current, "contacts", 10, 86400, global_limit=100):
            raise HTTPException(429, "Kontaktanfragenlimit erreicht. Bitte später erneut versuchen.")
        # Notification content never confirms the existence of the target account.
        try:
            accounts.send_notification(delivery, "HDGUI Kontaktanfrage",
                                       f"Eine Kontaktanfrage wartet in HDGUI. Melde dich an und prüfe Kontakte:\n{accounts.config.public_base_url}/contacts\nEs wurden noch keine Profile freigegeben.")
        except RuntimeError as exc:
            raise HTTPException(503, "Kontaktbenachrichtigung konnte nicht versendet werden. Bitte später erneut versuchen.") from exc
        try:
            with transaction(sessions, current) as session:
                session.execute(update(ContactRequest).where(
                    ContactRequest.status == "pending", ContactRequest.expires_at <= now(),
                ).values(status="expired"))
                target = session.scalar(select(User).where(User.email_key == email_key, User.status == "active"))
                low, high = pair(current.id, target.id) if target else (None, None)
                existing = session.scalar(select(ContactRequest).where(
                    ContactRequest.status == "pending",
                    or_(
                        (ContactRequest.requester_user_id == current.id) & (ContactRequest.target_email_key == email_key),
                        (ContactRequest.user_low_id == low) & (ContactRequest.user_high_id == high)
                        if target else False,
                    ),
                ))
                accepted = target and session.get(Contact, (low, high))
                if existing is None and not accepted:
                    session.add(ContactRequest(
                        id=str(uuid4()), requester_user_id=current.id, target_email_key=email_key,
                        target_user_id=target.id if target else None, user_low_id=low, user_high_id=high,
                        status="pending", created_at=now(), expires_at=now() + timedelta(days=14),
                    ))
                    audit(session, current.id, "contact_request", None)
        except IntegrityError:
            # Concurrent equivalent requests are the same logical invitation.
            with sessions() as session:
                duplicate = session.scalar(select(ContactRequest.id).where(
                    ContactRequest.requester_user_id == current.id,
                    ContactRequest.target_email_key == email_key, ContactRequest.status == "pending",
                ))
                if duplicate is None:
                    raise
        return RedirectResponse("/contacts", status_code=303)

    @router.post("/contacts/requests/{request_id}/{action}")
    async def respond(request: Request, request_id: str, action: str):
        form = await request.form()
        check_csrf(request, form)
        current = actor(request)
        with transaction(sessions, current) as session:
            invitation = session.scalar(select(ContactRequest).where(ContactRequest.id == request_id).with_for_update())
            recipient = invitation and invitation.target_email_key == normalize_email(current.email)[0]
            sender = invitation and invitation.requester_user_id == current.id
            if not invitation or invitation.status != "pending" or invitation.expires_at <= now():
                raise HTTPException(404, "Kontaktanfrage nicht gefunden.")
            if action not in ("accept", "decline", "cancel") or (action == "cancel" and not sender) or (action != "cancel" and not recipient):
                raise HTTPException(404, "Kontaktanfrage nicht gefunden.")
            if action == "accept":
                other = session.get(User, invitation.requester_user_id)
                if other is None or other.status != "active" or other.id == current.id:
                    raise HTTPException(404, "Kontaktanfrage nicht gefunden.")
                low, high = pair(other.id, current.id)
                if session.get(Contact, (low, high)) is None:
                    session.add(Contact(user_low_id=low, user_high_id=high, accepted_at=now()))
                invitation.target_user_id = current.id
                # Cancel duplicates targeting an account that signed up after issuance.
                session.execute(update(ContactRequest).where(
                    ContactRequest.id != invitation.id, ContactRequest.status == "pending",
                    or_(
                        (ContactRequest.requester_user_id == current.id) & (ContactRequest.target_email_key == other.email_key),
                        (ContactRequest.requester_user_id == other.id) & (ContactRequest.target_email_key == normalize_email(current.email)[0]),
                    ),
                ).values(status="cancelled"))
                invitation.status = "accepted"
            else:
                invitation.status = "declined" if action == "decline" else "cancelled"
            audit(session, current.id, f"contact_{action}", invitation.id)
        return RedirectResponse("/contacts", status_code=303)

    @router.post("/contacts/{user_id}/remove")
    async def remove_contact(request: Request, user_id: str):
        form = await request.form()
        check_csrf(request, form)
        current = actor(request)
        with transaction(sessions, current) as session:
            contact = session.get(Contact, pair(current.id, user_id))
            if contact is None:
                raise HTTPException(404, "Kontakt nicht gefunden.")
            shares = session.scalars(select(ProfileShare).where(or_(
                (ProfileShare.granting_owner_id == current.id) & (ProfileShare.recipient_user_id == user_id),
                (ProfileShare.granting_owner_id == user_id) & (ProfileShare.recipient_user_id == current.id),
            ))).all()
            for share in shares:
                revoke(session, share.profile_id, share.recipient_user_id)
            other = session.get(User, user_id)
            session.execute(delete(ContactRequest).where(or_(
                (ContactRequest.requester_user_id == current.id) & (ContactRequest.target_email_key == other.email_key),
                (ContactRequest.requester_user_id == user_id) & (ContactRequest.target_email_key == normalize_email(current.email)[0]),
            )))
            session.delete(contact)
            audit(session, current.id, "contact_remove", user_id)
        return RedirectResponse("/contacts", status_code=303)

    @router.get("/profiles/{profile_id}/sharing")
    def sharing(request: Request, profile_id: str, page: int = 1):
        if page < 1:
            raise HTTPException(422, "Ungültige Seite.")
        current = actor(request)
        with sessions() as session:
            profile = owned_profile(session, current, profile_id)
            request.state.read_profiles.add(profile_id)
            contacts = contacts_for(session, current.id, page)
            recipients = set(session.scalars(select(ProfileShare.recipient_user_id).where(
                ProfileShare.profile_id == profile_id, ProfileShare.granting_owner_id == current.id,
            )))
        return templates.TemplateResponse(request=request, name="profile_sharing.html", context={
            "profile": profile, "contacts": contacts, "recipients": recipients, "csrf": token(request), "page": page,
        })

    @router.post("/profiles/{profile_id}/sharing")
    async def grant(request: Request, profile_id: str):
        form = await request.form()
        check_csrf(request, form)
        current = actor(request)
        recipient_id = str(form.get("recipient_id", ""))
        if form.get("consent") != "yes":
            raise HTTPException(422, "Bitte die Freigabe der Profildaten ausdrücklich bestätigen.")
        with transaction(sessions, current) as session:
            owned_profile(session, current, profile_id)
            recipient = session.get(User, recipient_id)
            contact = session.get(Contact, pair(current.id, recipient_id))
            if not contact or not recipient or recipient.status != "active" or current.id == recipient_id:
                raise HTTPException(404, "Bestätigter Kontakt nicht gefunden.")
            share = session.scalar(select(ProfileShare).where(
                ProfileShare.profile_id == profile_id, ProfileShare.recipient_user_id == recipient_id,
            ))
            if share is None:
                session.add(ProfileShare(id=str(uuid4()), profile_id=profile_id,
                                         recipient_user_id=recipient_id, granting_owner_id=current.id, created_at=now()))
                audit(session, current.id, "profile_share", profile_id)
        return RedirectResponse(f"/profiles/{profile_id}/sharing", status_code=303)

    @router.post("/profiles/{profile_id}/sharing/{recipient_id}/revoke")
    async def remove_grant(request: Request, profile_id: str, recipient_id: str):
        form = await request.form()
        check_csrf(request, form)
        current = actor(request)
        with transaction(sessions, current) as session:
            owned_profile(session, current, profile_id)
            revoke(session, profile_id, recipient_id)
            audit(session, current.id, "profile_revoke", profile_id)
        return RedirectResponse(f"/profiles/{profile_id}/sharing", status_code=303)

    @router.post("/profiles/{profile_id}/sharing/remove")
    async def remove_incoming_share(request: Request, profile_id: str):
        form = await request.form()
        check_csrf(request, form)
        current = actor(request)
        with transaction(sessions, current) as session:
            share = session.scalar(select(ProfileShare).where(
                ProfileShare.profile_id == profile_id, ProfileShare.recipient_user_id == current.id,
            ))
            if share is None:
                raise HTTPException(404, "Freigabe nicht gefunden.")
            revoke(session, profile_id, current.id)
            audit(session, current.id, "profile_share_leave", profile_id)
        return RedirectResponse("/profiles?scope=shared", status_code=303)

    return router
