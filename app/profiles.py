import hashlib
import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator


class ProfileInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    birth_local: datetime
    timezone: str = Field(min_length=1, max_length=100)
    place: str = Field(min_length=1, max_length=200)

    @field_validator("name", "timezone", "place", mode="before")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_time(self):
        if self.birth_local.tzinfo is not None:
            raise ValueError("Bitte eine lokale Geburtszeit ohne UTC-Offset eingeben.")
        try:
            zone = ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unbekannte IANA-Zeitzone, z. B. Europe/Berlin.") from exc
        candidates = {
            local.astimezone(UTC)
            for fold in (0, 1)
            if (local := self.birth_local.replace(tzinfo=zone, fold=fold))
            .astimezone(UTC).astimezone(zone).replace(tzinfo=None) == self.birth_local
        }
        if not candidates:
            raise ValueError("Diese lokale Uhrzeit existiert wegen der Zeitumstellung nicht.")
        if len(candidates) > 1:
            raise ValueError(
                "Diese Uhrzeit ist wegen der Zeitumstellung mehrdeutig. "
                "Bitte die bekannte UTC-Zeit mit Zeitzone Etc/UTC eingeben."
            )
        return self

    def utc_time(self) -> datetime:
        return self.birth_local.replace(tzinfo=ZoneInfo(self.timezone)).astimezone(UTC)


def chart_key(birth_utc: datetime, api_url: str, engine_version: str) -> str:
    payload = json.dumps(
        [birth_utc.replace(tzinfo=UTC).isoformat(), api_url.rstrip("/"), engine_version, "svg-v1"]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def activation_key(birth_utc: datetime, api_url: str, engine_version: str) -> str:
    payload = json.dumps(
        [birth_utc.replace(tzinfo=UTC).isoformat(), api_url.rstrip("/"), engine_version,
         "activations-v1"]
    )
    return hashlib.sha256(payload.encode()).hexdigest()
