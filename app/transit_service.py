from datetime import UTC, datetime
from threading import Lock
from time import monotonic

from app.api_client import ChartClient
from app.bodygraph import render_bodygraph
from app.gate_descriptions import gate_descriptions
from app.transit import TransitFrame, render_wheel


class TransitCache:
    def __init__(self, client: ChartClient, descriptions=gate_descriptions):
        self.client = client
        self.descriptions = descriptions
        self._lock = Lock()
        self._cached: tuple[TransitFrame, str] | None = None
        self._started_at = 0.0
        self._bodygraph_frame: TransitFrame | None = None
        self._bodygraph = ""
        self._wheel_descriptions = None
        self._bodygraph_descriptions = None

    def bodygraph(self, frame: TransitFrame) -> str:
        with self._lock:
            descriptions = self.descriptions()
            if self._bodygraph_frame is not frame or self._bodygraph_descriptions != descriptions:
                self._bodygraph = render_bodygraph(frame, descriptions)
                self._bodygraph_frame = frame
                self._bodygraph_descriptions = descriptions
            return self._bodygraph

    def get(self, cache_seconds: int = 60) -> tuple[TransitFrame, str]:
        # Serialize refreshes so simultaneous viewers do not duplicate API calls.
        with self._lock:
            descriptions = self.descriptions()
            if self._cached is not None and monotonic() < self._started_at + cache_seconds:
                if self._wheel_descriptions != descriptions:
                    frame, _ = self._cached
                    self._cached = (frame, render_wheel(frame, descriptions))
                    self._wheel_descriptions = descriptions
                return self._cached
            started_at = monotonic()
            frame = self.client.transit(datetime.now(UTC))
            wheel = render_wheel(frame, descriptions)
            self._cached = (frame, wheel)
            self._started_at = started_at
            self._wheel_descriptions = descriptions
            return self._cached
