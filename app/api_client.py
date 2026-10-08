import httpx
from datetime import UTC, datetime
from pydantic import ValidationError
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from xml.etree.ElementTree import ParseError

from app.config import Settings
from app.models import BirthProfile
from app.transit import TransitFrame, TransitPosition


class ChartServiceError(Exception):
    pass


class ChartClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def transit(self, timestamp: datetime) -> TransitFrame:
        if not self.settings.api_token:
            raise ChartServiceError("HD_API_TOKEN ist nicht konfiguriert.")
        timestamp = timestamp.astimezone(UTC).replace(microsecond=0)
        params = {
            "year": timestamp.year, "month": timestamp.month, "day": timestamp.day,
            "hour": timestamp.hour, "minute": timestamp.minute, "second": timestamp.second,
            "place": "Etc/UTC", "latitude": 0, "longitude": 0,
        }
        try:
            response = httpx.get(
                f"{self.settings.api_url.rstrip('/')}/calculate",
                params=params,
                headers={"Authorization": f"Bearer {self.settings.api_token}"},
                timeout=60,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ChartServiceError(
                f"Der Transit-Dienst meldet HTTP {exc.response.status_code}."
            ) from exc
        except httpx.RequestError as exc:
            raise ChartServiceError("Der Transit-Dienst ist nicht erreichbar.") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise ChartServiceError("Der Transit-Dienst liefert kein lesbares JSON.") from exc
        try:
            positions = payload["gates"]["prs"]["Planets"]
            return TransitFrame(
                timestamp_utc=timestamp,
                positions=[TransitPosition.model_validate(item) for item in positions],
            )
        except (KeyError, TypeError, ValidationError) as exc:
            raise ChartServiceError("Die API-Antwort enthält keine gültigen Transitpositionen.") from exc

    def render(self, profile: BirthProfile) -> str:
        if not self.settings.api_token:
            raise ChartServiceError("HD_API_TOKEN ist nicht konfiguriert.")
        birth = profile.birth_utc
        # The upstream SVG endpoint truncates fractional timezone offsets.
        # Geocentric HD uses UTC at a zero-offset location to avoid that error.
        params = {
            "year": birth.year, "month": birth.month, "day": birth.day,
            "hour": birth.hour, "minute": birth.minute, "second": birth.second,
            "place": "Etc/UTC", "latitude": 0, "longitude": 0,
            "fmt": "svg",
        }
        try:
            response = httpx.get(
                f"{self.settings.api_url.rstrip('/')}/bodygraph",
                params=params,
                headers={"Authorization": f"Bearer {self.settings.api_token}"},
                timeout=60,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ChartServiceError(
                f"Der Chart-Dienst meldet HTTP {exc.response.status_code}."
            ) from exc
        except httpx.RequestError as exc:
            raise ChartServiceError("Der Chart-Dienst ist nicht erreichbar.") from exc
        try:
            root = ElementTree.fromstring(response.content)
        except (ParseError, DefusedXmlException) as exc:
            raise ChartServiceError("Der Chart-Dienst liefert kein lesbares SVG.") from exc
        if root.tag != "{http://www.w3.org/2000/svg}svg":
            raise ChartServiceError("Der Chart-Dienst liefert kein SVG.")
        return response.text
