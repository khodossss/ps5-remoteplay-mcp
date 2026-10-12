from __future__ import annotations

from ..config import Config
from .base import Button, ConsoleBackend, ConsoleError, ConsoleState, Frame, Side, Status


def make_backend(cfg: Config) -> ConsoleBackend:
    if cfg.backend == "fake":
        from .fake import FakeBackend

        return FakeBackend(cfg.fake_frames)
    from .remoteplay import RemotePlayBackend

    return RemotePlayBackend(cfg)


__all__ = [
    "Button",
    "ConsoleBackend",
    "ConsoleError",
    "ConsoleState",
    "Frame",
    "Side",
    "Status",
    "make_backend",
]
