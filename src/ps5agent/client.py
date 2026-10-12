"""HTTP client to the daemon; starts the daemon if it is not running."""

from __future__ import annotations

import asyncio
import base64
import os
import subprocess
import sys
import time
from dataclasses import dataclass

import httpx

from .config import Config

START_TIMEOUT_S = 20.0


class DaemonError(RuntimeError):
    pass


@dataclass(frozen=True)
class Snapshot:
    jpeg: bytes
    frame_id: int
    age_ms: int
    width: int
    height: int
    warnings: list[str]

    @classmethod
    def from_json(cls, data: dict) -> Snapshot:
        return cls(
            base64.b64decode(data["jpeg_b64"]),
            data["frame_id"],
            data["age_ms"],
            data["width"],
            data["height"],
            data.get("warnings", []),
        )

    def describe(self) -> str:
        text = f"frame_id={self.frame_id} age_ms={self.age_ms} size={self.width}x{self.height}"
        return text + "".join(f"\nWARNING: {w}" for w in self.warnings)


def spawn_daemon(cfg: Config) -> subprocess.Popen:
    cfg.config_dir.mkdir(parents=True, exist_ok=True)
    log = open(cfg.log_path, "ab")
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": log, "close_fds": True}
    cmd = [sys.executable, "-m", "ps5agent", "daemon"]
    if os.name != "nt":
        return subprocess.Popen(cmd, start_new_session=True, **kwargs)
    detached = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        # MCP clients on Windows kill the server's whole job on exit; the daemon must outlive it.
        return subprocess.Popen(cmd, creationflags=detached | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kwargs)
    except OSError:  # the job forbids breakaway
        return subprocess.Popen(cmd, creationflags=detached, **kwargs)


class DaemonClient:
    def __init__(self, cfg: Config, autostart: bool = True) -> None:
        self.cfg = cfg
        self.autostart = autostart
        self._http = httpx.AsyncClient(base_url=cfg.daemon_url, timeout=httpx.Timeout(90.0, connect=2.0))

    async def aclose(self) -> None:
        await self._http.aclose()

    async def ensure(self) -> None:
        try:
            await self._http.get("/status")
            return
        except httpx.TransportError:
            if not self.autostart:
                raise DaemonError(f"daemon not running at {self.cfg.daemon_url}") from None
        spawn_daemon(self.cfg)
        deadline = time.monotonic() + START_TIMEOUT_S
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            try:
                await self._http.get("/status")
                return
            except httpx.TransportError:
                continue
        raise DaemonError(f"daemon did not start; see {self.cfg.log_path}")

    async def _call(self, method: str, path: str, **kwargs) -> dict:
        await self.ensure()
        response = await self._http.request(method, path, **kwargs)
        data = response.json()
        if response.status_code >= 400 or data.get("ok") is False:
            raise DaemonError(data.get("error", f"HTTP {response.status_code}"))
        return data

    async def status(self) -> dict:
        return await self._call("GET", "/status")

    async def snapshot(self, max_width: int = 1024, quality: int = 80) -> Snapshot:
        data = await self._call("GET", "/snapshot", params={"max_width": max_width, "quality": quality})
        return Snapshot.from_json(data)

    async def input(self, op: str, **body) -> Snapshot | None:
        body = {k: v for k, v in body.items() if v is not None}
        data = await self._call("POST", "/input", json={"op": op, **body})
        return Snapshot.from_json(data["snapshot"]) if "snapshot" in data else None

    async def wake(self) -> None:
        await self._call("POST", "/wake")

    async def standby(self) -> None:
        await self._call("POST", "/standby")

    async def latency(self, samples: int = 10) -> dict:
        return await self._call("POST", "/latency", json={"samples": samples})

    async def shutdown(self) -> None:
        self.autostart = False
        await self._call("POST", "/shutdown")
