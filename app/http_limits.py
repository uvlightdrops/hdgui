"""Bound request bodies before form parsing or authentication."""
from starlette.responses import JSONResponse


class RequestTooLarge(Exception):
    pass


class RequestSizeLimit:
    def __init__(self, app, max_bytes):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        used = 0

        async def bounded_receive():
            nonlocal used
            message = await receive()
            if message["type"] == "http.request":
                used += len(message.get("body", b""))
                if used > self.max_bytes:
                    raise RequestTooLarge()
            return message

        try:
            await self.app(scope, bounded_receive, send)
        except RequestTooLarge:
            await JSONResponse({"detail": "Anfrage zu groß."}, status_code=413)(scope, receive, send)
