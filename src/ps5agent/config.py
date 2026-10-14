"""Configuration: environment variables, overridable by CLI flags."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("PS5_CONFIG_DIR", Path.home() / ".config" / "ps5-agent"))
BACKENDS = ("pyremoteplay", "fake")
RESOLUTIONS = ("360p", "540p", "720p", "1080p")
QUALITIES = ("default", "very_low", "low", "medium", "high", "very_high")


@dataclass(frozen=True)
class Config:
    host: str | None = None  # None: use the saved console or discover it on the LAN
    user: str | None = None
    backend: str = "pyremoteplay"
    resolution: str = "720p"
    fps: int = 30
    quality: str = "default"
    daemon_host: str = "127.0.0.1"
    daemon_port: int = 9395
    max_hold_ms: int = 10_000
    idle_exit_s: int = 600  # daemon exits (session closes) after this long without calls; 0 = never
    viewer: bool = False  # the daemon opens a window showing the console picture to the human
    fake_frames: str | None = None
    config_dir: Path = field(default=CONFIG_DIR)

    @property
    def daemon_url(self) -> str:
        return f"http://{self.daemon_host}:{self.daemon_port}"

    @property
    def profile_path(self) -> Path:
        return self.config_dir / "profiles.json"

    @property
    def log_path(self) -> Path:
        return self.config_dir / "daemon.log"

    def validate(self) -> Config:
        if self.backend not in BACKENDS:
            raise ValueError(f"PS5_BACKEND must be one of {BACKENDS}")
        if self.resolution not in RESOLUTIONS:
            raise ValueError(f"PS5_RESOLUTION must be one of {RESOLUTIONS}")
        if self.fps not in (30, 60):
            raise ValueError("PS5_FPS must be 30 or 60")
        if self.quality not in QUALITIES:
            raise ValueError(f"PS5_QUALITY must be one of {QUALITIES}")
        if self.daemon_host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("the daemon API listens on loopback only")
        return self

    def with_overrides(self, **overrides: object) -> Config:
        return replace(self, **{k: v for k, v in overrides.items() if v is not None})


def from_env(env: dict[str, str] | None = None) -> Config:
    env = dict(os.environ if env is None else env)
    addr = env.get("PS5_DAEMON_ADDR", "127.0.0.1:9395")
    daemon_host, _, port = addr.rpartition(":")
    return Config(
        host=env.get("PS5_HOST") or None,
        user=env.get("PS5_USER") or None,
        backend=env.get("PS5_BACKEND", "pyremoteplay"),
        resolution=env.get("PS5_RESOLUTION", "720p"),
        fps=int(env.get("PS5_FPS", "30")),
        quality=env.get("PS5_QUALITY", "default").lower(),
        daemon_host=daemon_host or "127.0.0.1",
        daemon_port=int(port),
        max_hold_ms=int(env.get("PS5_MAX_HOLD_MS", "10000")),
        idle_exit_s=int(env.get("PS5_IDLE_EXIT_S", "600")),
        viewer=env.get("PS5_VIEWER", "").lower() in ("1", "true", "yes", "on"),
        fake_frames=env.get("PS5_FAKE_FRAMES") or None,
        config_dir=Path(env.get("PS5_CONFIG_DIR", CONFIG_DIR)),
    )
