"""first_piece: waits for the first piece, and stops the pending one when the caller hangs up."""
import asyncio

import pytest

from disconnect import CallerGone, first_piece


class FakeRequest:
    def __init__(self, gone_after: float | None) -> None:
        self.gone_after = gone_after
        self.started = asyncio.get_running_loop().time()

    async def is_disconnected(self) -> bool:
        return self.gone_after is not None and asyncio.get_running_loop().time() - self.started >= self.gone_after


async def slow(delay: float, log: list[str]):
    try:
        await asyncio.sleep(delay)
        yield "first"
        yield "second"
    finally:
        log.append("closed")


def test_returns_the_first_piece_when_the_caller_stays():
    async def run():
        log: list[str] = []
        assert await first_piece(FakeRequest(None), slow(0.05, log)) == "first"
    asyncio.run(run())


def test_default_is_returned_for_an_empty_stream():
    async def empty():
        return
        yield

    async def run():
        assert await first_piece(FakeRequest(None), empty(), None) is None
    asyncio.run(run())


def test_hang_up_cancels_the_pending_piece():
    async def run():
        log: list[str] = []
        with pytest.raises(CallerGone):
            await asyncio.wait_for(first_piece(FakeRequest(0.1), slow(30, log)), 5)
        assert log == ["closed"]  # the generator ended: it would run 30 s otherwise
    asyncio.run(run())
