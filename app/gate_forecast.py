from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Event, Lock
from time import monotonic

from pydantic import BaseModel

from app.api_client import ChartClient, ChartServiceError
from app.transit import GATES, TransitFrame


# Conservative absolute geocentric speeds in degrees/day, in either direction.
MAX_SPEED = {
    "Sun": 1.2, "Earth": 1.2, "Moon": 18, "Mercury": 3, "Venus": 2,
    "Mars": 1, "Jupiter": .4, "Saturn": .2, "Uranus": .1,
    "Neptune": .08, "Pluto": .08, "North_Node": .4, "South_Node": .4,
}
GATE_WIDTH = 360 / 64
RESOLUTION = timedelta(seconds=30)
HORIZON = timedelta(days=3653)


class GateChange(BaseModel):
    body: str
    current_gate: int
    next_gate: int
    eta_utc: datetime


def find_changes(client: ChartClient, start: TransitFrame, stop: Event, publish):
    pending = {position.body: position.gate for position in start.positions}
    if len(pending) != len(start.positions) or pending.keys() - MAX_SPEED.keys():
        raise ChartServiceError("Unbekannte oder doppelte Himmelskörper in der Gate-Prognose.")
    previous = start
    end = start.timestamp_utc + HORIZON
    requests = 0

    def fetch(timestamp):
        nonlocal requests
        if stop.is_set():
            raise ChartServiceError("Gate-Berechnung wurde beendet.")
        requests += 1
        if requests > 10000:
            raise ChartServiceError("Gate-Suche hat das Anfragelimit erreicht.")
        frame = client.transit(timestamp)
        positions = {position.body: position for position in frame.positions}
        if not pending.keys() <= positions.keys():
            raise ChartServiceError("In zukünftigen Transitdaten fehlen Himmelskörper.")
        return frame, positions

    while pending:
        if stop.is_set():
            return
        positions = {position.body: position for position in previous.positions}
        steps = []
        for body in pending:
            position = positions[body]
            index = GATES.index(position.gate)
            offset = (position.longitude - (302 + index * GATE_WIDTH)) % 360
            # API longitudes are rounded to 0.001 degrees. Gate IDs are authoritative.
            if offset > GATE_WIDTH and offset < 359.999:
                raise ChartServiceError("Gate-Grenzen der API stimmen nicht mit dem Rad überein.")
            distance = max(0, min(offset, GATE_WIDTH - offset) - .001)
            # Remain inside both boundaries until close enough to scan at 30s resolution.
            steps.append(distance / MAX_SPEED[body] * 86400 * .8)
        seconds = max(RESOLUTION.total_seconds(), min(min(steps), 7 * 86400))
        timestamp = min(previous.timestamp_utc + timedelta(seconds=int(seconds)), end)
        current, positions = fetch(timestamp)
        for body, original_gate in list(pending.items()):
            if positions[body].gate == original_gate:
                continue
            low, high = previous.timestamp_utc, current.timestamp_utc
            target = positions[body]
            while high - low > RESOLUTION:
                middle = low + timedelta(seconds=int((high - low).total_seconds() / 2))
                _, sampled = fetch(middle)
                if sampled[body].gate == original_gate:
                    low = middle
                else:
                    high, target = middle, sampled[body]
            publish(GateChange(body=body, current_gate=original_gate,
                               next_gate=target.gate, eta_utc=high))
            del pending[body]
        if current.timestamp_utc >= end and pending:
            raise ChartServiceError("Kein Gate-Wechsel innerhalb des Suchhorizonts von 10 Jahren gefunden.")
        previous = current


class GateForecastService:
    def __init__(self, client: ChartClient):
        self.client = client
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gate-forecast")
        self._stop = Event()
        self._future = None
        self._start: datetime | None = None
        self._changes: dict[str, GateChange] = {}
        self._error: str | None = None
        self._retry_at = 0.0

    def close(self):
        self._stop.set()
        self._executor.shutdown(wait=True, cancel_futures=True)

    def snapshot(self, frame: TransitFrame):
        with self._lock:
            running = self._future is not None and not self._future.done()
            if self._future is not None and self._future.done():
                self._future.result()
            if self._error and monotonic() >= self._retry_at:
                self._error = None
            valid = {
                body: change for body, change in self._changes.items()
                if change.eta_utc > frame.timestamp_utc
                and self._start is not None and frame.timestamp_utc >= self._start
                and any(p.body == body and p.gate == change.current_gate for p in frame.positions)
            }
            if not running and not self._error and len(valid) != len(frame.positions):
                self._changes = valid
                self._start = frame.timestamp_utc
                missing = frame.model_copy(update={
                    "positions": [p for p in frame.positions if p.body not in valid]
                })
                self._future = self._executor.submit(self._calculate, missing)
                running = True
            return {
                "changes": [change.model_dump(mode="json") for change in valid.values()],
                "pending": running,
                "error": self._error,
            }

    def _calculate(self, frame):
        try:
            find_changes(self.client, frame, self._stop, self._publish)
        except ChartServiceError as exc:
            with self._lock:
                self._error = str(exc)
                self._retry_at = monotonic() + 60

    def _publish(self, change):
        with self._lock:
            self._changes[change.body] = change
