import os
import secrets
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.getenv("HDGUI_DATABASE_URL", "sqlite:///data/hdgui.db")
    )
    api_url: str = field(
        default_factory=lambda: os.getenv("HDGUI_API_URL", "http://127.0.0.1:9021")
    )
    api_token: str = field(default_factory=lambda: os.getenv("HD_API_TOKEN", ""))
    engine_version: str = field(
        default_factory=lambda: os.getenv("HDGUI_ENGINE_VERSION", "4.0.2")
    )
    session_secret: str = field(
        default_factory=lambda: os.getenv("HDGUI_SESSION_SECRET") or secrets.token_hex(32)
    )
