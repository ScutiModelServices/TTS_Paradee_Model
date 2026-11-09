"""Update state and sequencing for the model files; checks never download them."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import time
from typing import Any, Awaitable, Callable

from progress import progress


class Updates:
    """Inject async revision, download and unload operations; serialize checks and updates per replica.
    `description` and `reload_note` are the texts the admin page shows; each service words them for its own backend."""

    def __init__(self, model: str, *, cached_sha: Callable[[], Awaitable[str | None]],
                 remote_sha: Callable[[], Awaitable[str]], download: Callable[[], Awaitable[None]],
                 unload: Callable[[], Awaitable[None]],
                 description: str = "Looks for newer model files on the Hugging Face Hub without downloading.",
                 reload_note: str = "Files downloaded. Every replica drops the old copy within a few seconds and loads the new files on its next request.") -> None:
        self._model = model
        self._description, self._reload_note = description, reload_note
        self._cached_sha, self._remote_sha = cached_sha, remote_sha
        self._download, self._unload = download, unload
        self._lock = asyncio.Lock()
        self._unload_pending = False
        self._status: dict[str, Any] = {"checked_at": None, "error": None, "package": None, "weights": []}

    def status(self) -> dict[str, Any]:
        """Return the last result without I/O or exposing mutable internal state."""
        return {**deepcopy(self._status), "progress": progress.snapshot(),
                "description": self._description, "reload_note": self._reload_note}

    async def check(self) -> dict[str, Any]:
        async with self._lock:
            return await self._check()

    async def _check(self) -> dict[str, Any]:
        status: dict[str, Any] = {"checked_at": time.time(), "error": None, "package": None, "weights": []}
        cached, latest = None, None
        errors = []
        try:
            cached = await self._cached_sha()
        except Exception as error:
            errors.append(f"{type(error).__name__}: {error}")
        try:
            latest = await self._remote_sha()
        except Exception as error:
            errors.append(f"{type(error).__name__}: {error}")
        status["weights"] = [{"repo": self._model, "cached": cached, "latest": latest,
                              "update": cached is None or (latest is not None and cached != latest) or self._unload_pending}]
        status["error"] = "; ".join(errors) or None
        self._status = status
        return self.status()

    async def update_weights(self) -> dict[str, Any]:
        """Download changed weights, then make every replica drop the old copy."""
        async with self._lock:
            try:
                status = await self._check()
                # Metadata failures or local imports must not block the explicit admin download.
                # A known equal revision only needs unloading, including retry after a lost response.
                if any(item["latest"] is None or item["cached"] != item["latest"] for item in status["weights"]):
                    self._unload_pending = True
                    await self._download()
                # Always unload on this explicit admin action. A retry may reach a different replica
                # after a successful download or failed unload; equal revisions do not prove the loaded copy is current.
                self._unload_pending = True
                await self._unload()
                self._unload_pending = False
                return await self._check()
            except Exception as error:
                self._status = {**self._status, "error": f"{type(error).__name__}: {error}"}
                raise
