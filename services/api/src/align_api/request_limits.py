"""Bound incoming bodies before JSON hashing or multipart disk spooling."""

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyLimit:
    def __init__(self, app: ASGIApp, upload_bytes: int) -> None:
        self.app = app
        self.upload_bytes = upload_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        path = scope['path']
        if path.endswith('/context-files'):
            limit = self.upload_bytes + 64 * 1024  # one file plus multipart metadata
        elif path in {'/api/v1/auth/login', '/api/v1/me/password'}:
            limit = 8192
        else:
            limit = 1024 * 1024
        headers = dict(scope.get('headers', []))
        try:
            declared = int(headers.get(b'content-length', b'0'))
        except ValueError:
            declared = -1
        if declared < 0 or declared > limit:
            await self.reject(scope, receive, send, 400 if declared < 0 else 413)
            return
        received = 0

        async def bounded_receive() -> Message:
            nonlocal received
            message = await receive()
            if message['type'] == 'http.request':
                received += len(message.get('body', b''))
                if received > limit:
                    raise HTTPException(status_code=413)
            return message

        await self.app(scope, bounded_receive, send)

    @staticmethod
    async def reject(scope: Scope, receive: Receive, send: Send, status: int) -> None:
        from uuid import uuid4

        response = JSONResponse(
            {'error': {
                'code': 'PAYLOAD_TOO_LARGE' if status == 413 else 'INVALID_REQUEST',
                'message': 'Ukuran permintaan melebihi batas.' if status == 413 else 'Permintaan tidak valid.',
                'field_errors': [], 'request_id': str(uuid4()),
            }}, status_code=status, headers={'Cache-Control': 'private, no-store'},
        )
        await response(scope, receive, send)
