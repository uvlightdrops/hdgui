"""Typed individual-chart values from /calculate, without imported interpretation texts."""
from datetime import UTC, datetime
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import CENTER_IDS
from app.transit import TransitPosition

PROFILE_CODES = {"1/3", "1/4", "2/4", "2/5", "3/5", "3/6", "4/6", "4/1", "5/1", "5/2", "6/2", "6/3"}
CENTER_ALIASES = {
    **{center: center for center in CENTER_IDS},
    "Solar Plexus": "Solar", "G_Center": "G", "G Center": "G",
    "Ego": "Heart", "Heart/Ego": "Heart",
}


class NatalPosition(TransitPosition):
    color: int | None = Field(default=None, alias="Color", ge=1, le=6, strict=True)
    tone: int | None = Field(default=None, alias="Tone", ge=1, le=6, strict=True)
    base: int | None = Field(default=None, alias="Base", ge=1, le=5, strict=True)
    harmonic_gate: int | None = Field(default=None, alias="Ch_Gate", ge=1, le=64, strict=True)

    @field_validator("harmonic_gate", mode="before")
    @classmethod
    def absent_harmonic_gate(cls, value: int | None) -> int | None:
        # The legacy endpoint uses 0 for an absent single harmonic-gate value.
        return None if type(value) is int and value == 0 else value


class Variable(BaseModel):
    value: Literal["left", "right"]
    name: str = Field(min_length=1, max_length=100)
    aspect: str = Field(min_length=1, max_length=100)
    def_type: str = Field(min_length=1, max_length=100)


class Variables(BaseModel):
    top_left: Variable
    bottom_left: Variable
    bottom_right: Variable
    top_right: Variable
    short_code: str = Field(min_length=1, max_length=40)


class NatalDetails(BaseModel):
    """Known general fields; unrecognized backend fields stay in raw_response."""
    model_config = ConfigDict(str_strip_whitespace=True)

    birth_date: datetime
    create_date: datetime
    energy_type: str = Field(min_length=1, max_length=100)
    strategy: str = Field(min_length=1, max_length=200)
    signature: str = Field(min_length=1, max_length=100)
    not_self: str = Field(min_length=1, max_length=100)
    aura: str = Field(min_length=1, max_length=200)
    inner_authority: str = Field(min_length=1, max_length=100)
    inc_cross: str = Field(min_length=1, max_length=200)
    profile: str = Field(min_length=1, max_length=200)
    definition: str = Field(min_length=1, max_length=100)
    defined_centers: list[str]
    undefined_centers: list[str]
    variables: Variables
    zodiac_sign: str | None = None

    @field_validator("birth_date", "create_date")
    @classmethod
    def utc_date(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Geburts- und Designzeit benötigen einen UTC-Offset.")
        return value.astimezone(UTC)

    @field_validator("profile")
    @classmethod
    def valid_profile(cls, value: str) -> str:
        match = re.fullmatch(r"([1-6]/[1-6])(?:\s*:.*)?", value)
        if match is None or match[1] not in PROFILE_CODES:
            raise ValueError("Unbekannte Profil-Linienkombination.")
        return value

    @field_validator("defined_centers", "undefined_centers")
    @classmethod
    def normalize_centers(cls, values: list[str]) -> list[str]:
        if any(value not in CENTER_ALIASES for value in values):
            raise ValueError("Unbekannte Zentrum-Kennung.")
        result = [CENTER_ALIASES[value] for value in values]
        if len(set(result)) != len(result):
            raise ValueError("Doppelte Zentrum-Kennung.")
        return result

    @model_validator(mode="after")
    def complete_centers_and_dates(self) -> "NatalDetails":
        defined, undefined = set(self.defined_centers), set(self.undefined_centers)
        if defined & undefined or defined | undefined != set(CENTER_IDS):
            raise ValueError("Definierte und undefinierte Zentren müssen die neun Zentren partitionieren.")
        if self.create_date >= self.birth_date:
            raise ValueError("Designzeit muss vor der Geburt liegen.")
        return self

    @property
    def profile_code(self) -> str:
        return self.profile.split(":", 1)[0].strip()
