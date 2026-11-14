"""Locale negotiation and fallback for the shared knowledge catalog."""
from dataclasses import dataclass
import re
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KnowledgeEntityText, UiSettings

DEFAULT_LOCALE = "de"
LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


@dataclass(frozen=True)
class ResolvedText:
    record: KnowledgeEntityText
    requested_locale: str

    @property
    def is_fallback(self) -> bool:
        return self.record.locale != self.requested_locale


def normalize_locale(locale: str) -> str | None:
    """Return a normalized BCP-47-like locale tag, or None when malformed."""
    candidate = locale.strip().replace("_", "-")
    if not LANGUAGE_TAG.fullmatch(candidate):
        return None
    parts = candidate.split("-")
    normalized = [parts[0].lower()]
    for part in parts[1:]:
        normalized.append(part.upper() if len(part) == 2 and part.isalpha() else part)
    return "-".join(normalized)


def locale_candidates(locale: str, default_locale: str = DEFAULT_LOCALE) -> tuple[str, ...]:
    """Return exact-to-general fallback locales, ending at the configured default."""
    normalized = normalize_locale(locale) or default_locale
    candidates = [normalized]
    while "-" in normalized:
        normalized = normalized.rsplit("-", 1)[0]
        candidates.append(normalized)
    if default_locale not in candidates:
        candidates.append(default_locale)
    return tuple(candidates)


def negotiate_locale(header: str | None) -> str:
    """Choose the best well-formed language range from Accept-Language."""
    preferences = []
    for order, entry in enumerate((header or "").split(",")):
        language, *parameters = entry.strip().split(";")
        locale = normalize_locale(language)
        if locale is None:
            continue
        quality = 1.0
        for parameter in parameters:
            key, separator, value = parameter.strip().partition("=")
            if separator and key == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if quality > 0:
            preferences.append((-quality, order, locale))
    return min(preferences)[2] if preferences else DEFAULT_LOCALE


def effective_locale(session: Session, accept_language: str | None = None) -> str:
    """Use a saved preference, then the request language, then German."""
    preferences = session.get(UiSettings, 1)
    if preferences and preferences.catalog_locale:
        locale = normalize_locale(preferences.catalog_locale)
        if locale:
            return locale
    return negotiate_locale(accept_language)


def resolve_texts(
    session: Session,
    entity_ids: set[str] | list[str] | tuple[str, ...],
    locale: str,
    text_kind: str = "human-design",
) -> dict[str, ResolvedText]:
    """Resolve localized records in one query and retain their actual locale."""
    if not entity_ids:
        return {}
    rows = session.scalars(select(KnowledgeEntityText).where(
        KnowledgeEntityText.entity_id.in_(entity_ids),
        KnowledgeEntityText.text_kind == text_kind,
    )).all()
    by_key = {(row.entity_id, row.locale): row for row in rows}
    result = {}
    for entity_id in entity_ids:
        for candidate in locale_candidates(locale):
            record = by_key.get((entity_id, candidate))
            if record is not None:
                result[entity_id] = ResolvedText(record, locale)
                break
    return result


def localized_record(record, resolved: ResolvedText | None) -> SimpleNamespace:
    """Adapt legacy structural records for templates without mutating ORM state."""
    fields = {
        column.key: getattr(record, column.key)
        for column in record.__mapper__.column_attrs
    }
    if resolved is not None:
        text = resolved.record
        fields.update({
            "name": text.name,
            "description": text.description,
            "description_origin": text.origin,
            "description_author": text.author,
            "description_model": text.model,
            "description_date": text.authored_at,
            "review_status": text.review_status,
            "metadata_json": text.metadata_json,
            "text_locale": text.locale,
            "text_is_fallback": resolved.is_fallback,
        })
    else:
        fields.update({
            "text_locale": None, "text_is_fallback": True, "metadata_json": {},
        })
    return SimpleNamespace(**fields)


def upsert_text(
    session: Session,
    entity_id: str,
    *,
    name: str | None,
    description: str | None,
    text_kind: str = "human-design",
    locale: str = DEFAULT_LOCALE,
    origin: str = "legacy",
    author: str | None = None,
    model: str | None = None,
    authored_at: str | None = None,
    review_status: str = "unknown",
    metadata: dict | None = None,
    replace_human: bool = False,
) -> KnowledgeEntityText:
    """Write one localized record while protecting explicitly human-authored text."""
    normalized_locale = normalize_locale(locale)
    if normalized_locale is None:
        raise ValueError(f"Ungültige BCP-47-Sprachkennung: {locale!r}")
    locale = normalized_locale
    key = (entity_id, locale, text_kind)
    record = session.get(KnowledgeEntityText, key)
    if record is None:
        record = KnowledgeEntityText(
            entity_id=entity_id, locale=locale, text_kind=text_kind,
            name=name, description=description, metadata_json=metadata or {},
            origin=origin, author=author, model=model, authored_at=authored_at,
            review_status=review_status,
        )
        session.add(record)
        return record
    if record.origin == "human" and origin != "human" and not replace_human:
        raise ValueError(f"{entity_id}: eigener Text geschützt; Ersetzung nur mit Freigabe.")
    record.name = name
    record.description = description
    record.metadata_json = metadata or {}
    record.origin = origin
    record.author = author
    record.model = model
    record.authored_at = authored_at
    record.review_status = review_status
    return record
