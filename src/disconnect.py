"""Wait for the first piece of a stream, but give up as soon as the caller hangs up.

A route that waits for the first piece before it returns its streaming response is not told about a hang-up during
that wait, so a slow first piece (a buffered answer, a long first chunk of speech) kept the model busy for a caller
who had gone. Cancelling the pending piece stops the generator the same way closing it does."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from fastapi import Request

POLL_SECONDS = 0.25
_NONE = object()


class CallerGone(Exception):
    """The caller disconnected before the first piece was ready."""


async def first_piece(request: Request, pieces: AsyncIterator[Any], default: Any = _NONE) -> Any:
    """`await anext(pieces, default)` (StopAsyncIteration without a default), or CallerGone when the caller disconnects first.
    The pending piece is cancelled, so the generator ends and releases its slot; a model call already running in a
    worker thread finishes its current step on its own."""
    task = asyncio.ensure_future(anext(pieces) if default is _NONE else anext(pieces, default))
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=POLL_SECONDS)
            if done:
                return task.result()
            if await request.is_disconnected():
                raise CallerGone
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


class Measure:
    """Times /v1/ calls and records token usage. Plain ASGI on purpose: the `@app.middleware("http")` form wraps
    `receive`, which hides a hang-up from `request.is_disconnected()` for the whole call. Recorded when the response
    starts, so a stream is timed to its first byte."""

    def __init__(self, app: Any, observe: Callable[[float], None],
                 record: Callable[[str, float, bool], Awaitable[Any]]) -> None:
        self.app, self.observe, self.record = app, observe, record

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()

        async def tracked(message: dict) -> None:
            if message["type"] == "http.response.start":
                elapsed = time.perf_counter() - started
                if scope["path"].startswith("/v1/"):
                    self.observe(elapsed)
                if token_id := scope.get("state", {}).get("token_id"):
                    await self.record(token_id, elapsed * 1000, message["status"] >= 500)
            await send(message)

        await self.app(scope, receive, tracked)
