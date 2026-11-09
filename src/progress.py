"""Progress of the running weight download, shared by every downloader (Hub, Ollama, plain HTTP, Google Drive)."""

from __future__ import annotations

from contextlib import contextmanager
from fnmatch import fnmatch
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Iterable, Iterator


class Progress:
    """Bytes of the running weight download. Replicas share it through a small file next to the weights marker,
    so the admin page sees it whichever replica answers; without a shared folder each replica shows its own."""

    STALE = 30.0  # seconds without an update before a leftover file is ignored (a crashed download)

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {"active": False, "label": None, "done": 0, "total": None, "updated": 0.0}
        self._written = 0.0

    @staticmethod
    def _path() -> Path:
        marker = os.environ.get("SERVICE_WEIGHTS_MARKER")
        return (Path(marker).parent if marker else Path(os.environ.get("HF_HOME") or "~/.cache/huggingface").expanduser()) / ".download-progress.json"

    def _write(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._written < 0.5:
            return
        self._written = now
        try:
            self._path().write_text(json.dumps(self._state), "utf-8")
        except OSError:
            pass  # shortcut: no shared folder, so only this replica sees the numbers

    def start(self, label: str, total: int | None) -> None:
        with self._lock:
            self._state = {"active": True, "label": label, "done": 0, "total": total or None, "updated": time.time()}
            self._write(True)

    def set(self, done: int, total: int | None = None) -> None:
        with self._lock:
            self._state = {**self._state, "done": done, "updated": time.time(), **({"total": total} if total else {})}
            self._write()

    def finish(self) -> None:
        with self._lock:
            self._state = {**self._state, "active": False, "updated": time.time()}
            self._write(True)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = self._state
        if not state["active"]:
            try:
                shared = json.loads(self._path().read_text("utf-8"))
                if shared.get("active") and time.time() - shared.get("updated", 0) < self.STALE:
                    state = shared
            except (OSError, ValueError):
                pass
        return {"active": bool(state["active"]), "label": state["label"], "done": state["done"], "total": state["total"]}


progress = Progress()


def _size(folder: Path) -> int:
    return sum(os.lstat(os.path.join(root, name)).st_size for root, _, names in os.walk(folder) for name in names if os.path.lexists(os.path.join(root, name)))


def hub_blobs(repo: str, cache_dir: str | os.PathLike[str] | None = None) -> Path:
    """The folder where the Hub client writes the files of `repo` (partial files grow here)."""
    from huggingface_hub import constants

    return Path(cache_dir or constants.HF_HUB_CACHE) / ("models--" + repo.replace("/", "--")) / "blobs"


def hub_total(repo: str, revision: str | None, patterns: list[str] | None = None) -> int | None:
    """Bytes of the files a download will fetch, or None when the Hub does not say."""
    try:
        from huggingface_hub import HfApi

        info = HfApi().model_info(repo, revision=revision, files_metadata=True, token=False)
        sizes = [file.size for file in info.siblings if not patterns or any(fnmatch(file.rfilename, p) for p in patterns)]
        return sum(sizes) if sizes and None not in sizes else None
    except Exception:
        return None


class PullTracker:
    """Turns the JSON lines of Ollama's streaming /api/pull into progress; `status` is the last status ("success" at the end)."""

    def __init__(self, label: str) -> None:
        self._layers: dict[str, tuple[int, int]] = {}
        self.status: str | None = None
        progress.start(label, None)

    def feed(self, raw: bytes | str) -> None:
        if not raw.strip():
            return
        event = json.loads(raw)
        if event.get("error"):
            raise RuntimeError(str(event["error"]))
        self.status = event.get("status", self.status)
        if event.get("digest") and event.get("total"):
            self._layers[event["digest"]] = (event.get("completed") or 0, event["total"])
            progress.set(sum(done for done, _ in self._layers.values()), sum(total for _, total in self._layers.values()))

    def close(self) -> None:
        progress.finish()


def ollama_pull_progress(lines: Iterable[bytes | str], label: str) -> str | None:
    """Feed every line to a PullTracker; returns the last status."""
    tracker = PullTracker(label)
    try:
        for raw in lines:
            tracker.feed(raw)
    finally:
        tracker.close()
    return tracker.status


@contextmanager
def tracking(label: str, total: int | None, folder: Path) -> Iterator[None]:
    """Report the growth of `folder` as download progress while the block runs. Works for any downloader."""
    start = _size(folder) if folder.exists() else 0
    stop = threading.Event()

    def poll() -> None:
        while not stop.wait(0.5):
            if folder.exists():
                progress.set(max(0, _size(folder) - start))

    progress.start(label, total)
    thread = threading.Thread(target=poll, daemon=True)
    thread.start()
    try:
        yield
        if total:
            progress.set(total)
    finally:
        stop.set()
        thread.join()
        progress.finish()
