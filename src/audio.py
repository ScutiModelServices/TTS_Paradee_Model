"""WAV helpers and the answer cache shared by the text-to-speech services (the same file in TTS_Piper_Model and TTS_Paradee_Model)."""

from __future__ import annotations

import struct
from collections import OrderedDict
from typing import Any


def wav_header(rate: int, size: int | None = None) -> bytes:
    """Header of a 16-bit mono WAV. `size` is the byte length of the audio; None means unknown (a stream),
    which players read as "until the connection ends"."""
    unknown = 0xFFFFFFFF
    return struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", unknown if size is None else 36 + size, b"WAVE", b"fmt ", 16, 1, 1,
                       rate, rate * 2, 2, 16, b"data", unknown if size is None else size)


def wav_bytes(pcm: bytes, rate: int) -> bytes:
    return wav_header(rate, len(pcm)) + pcm


class AudioCache:
    """The last answers, least recently used out. Per replica and in memory, so with N replicas the hit rate is
    about 1/N; an answer over MAX_BYTES is not kept, so `size` entries cost at most size * MAX_BYTES.

    shortcut: no shared store between replicas, upgrade only if the hit rate matters."""

    MAX_BYTES = 1_000_000  # about 20 s of audio

    def __init__(self) -> None:
        self._items: OrderedDict[Any, dict[str, Any]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._items)

    def get(self, key: Any, size: int) -> dict[str, Any] | None:
        """The kept answer for `key`, or None. `size` 0 turns the cache off and empties it."""
        if not size:
            self._items.clear()
            return None
        item = self._items.get(key)
        if item is not None:
            self._items.move_to_end(key)
        return item

    def put(self, key: Any, item: dict[str, Any], size: int) -> None:
        if not size or len(item["wav"]) > self.MAX_BYTES:
            return
        self._items[key] = item
        self._items.move_to_end(key)
        while len(self._items) > size:
            self._items.popitem(last=False)
