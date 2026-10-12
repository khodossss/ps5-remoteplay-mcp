"""Phase 0 spike: prove pyremoteplay works against a real PS5.

Prereq: client registered once via `uv run python -m pyremoteplay <IP> --register`.
Usage:  uv run python tools/spike.py --host <IP> [--user <PSN name>] [--out spike_out]
Gate: steps 1-4 pass. Writes spike_out/report.json + PNG frames.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import statistics
import threading
import time
from pathlib import Path

import numpy as np
from pyremoteplay import RPDevice
from pyremoteplay.receiver import QueueReceiver

DIFF_SIZE = (160, 90)  # downscaled gray frame used for change detection
PIXEL_DELTA = 25  # gray-level change that counts a pixel as changed


class StampedReceiver(QueueReceiver):
    """QueueReceiver that also records a monotonic timestamp and counter per frame."""

    def __init__(self) -> None:
        super().__init__(max_frames=30)
        self._lock = threading.Lock()
        self.frame_id = 0
        self.frame_ts = 0.0

    def handle_video(self, frame) -> None:
        with self._lock:
            self.frame_id += 1
            self.frame_ts = time.monotonic()
        super().handle_video(frame)

    def latest(self):
        with self._lock:
            return self.frame_id, self.frame_ts, self.get_latest_video_frame()


def small_gray(frame) -> np.ndarray:
    return (
        frame.reformat(width=DIFF_SIZE[0], height=DIFF_SIZE[1], format="gray").to_ndarray().astype(np.int16)
    )


def changed_fraction(a: np.ndarray, b: np.ndarray) -> float:
    return float((np.abs(a - b) > PIXEL_DELTA).mean())


async def wait_frame_after(rx: StampedReceiver, after_id: int, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        fid, ts, frame = rx.latest()
        if fid > after_id and frame is not None:
            return fid, ts, frame
        await asyncio.sleep(0.005)
    return None


async def wait_change(rx: StampedReceiver, base: np.ndarray, after_id: int, threshold: float, timeout: float):
    """Return (frame_ts, fraction) of the first frame after `after_id` that differs from `base`."""
    deadline = time.monotonic() + timeout
    seen = after_id
    while time.monotonic() < deadline:
        fid, ts, frame = rx.latest()
        if fid > seen and frame is not None:
            seen = fid
            frac = changed_fraction(base, small_gray(frame))
            if frac > threshold:
                return ts, frac
        await asyncio.sleep(0.005)
    return None, 0.0


async def noise_floor(rx: StampedReceiver, seconds: float = 2.0) -> float:
    """Max changed fraction between consecutive frames while idle (animated backgrounds)."""
    fracs, prev, last_id = [], None, 0
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        got = await wait_frame_after(rx, last_id, timeout=1.0)
        if not got:
            break
        last_id, _, frame = got
        cur = small_gray(frame)
        if prev is not None:
            fracs.append(changed_fraction(prev, cur))
        prev = cur
    return max(fracs, default=0.0)


async def press_and_detect(device, rx, button, threshold, timeout=3.0):
    fid, _, frame = await wait_frame_after(rx, 0)
    base = small_gray(frame)
    t0 = time.monotonic()
    await device.controller.async_button(button, "tap", delay=0.08)
    ts, frac = await wait_change(rx, base, fid, threshold, timeout)
    return (ts - t0) * 1000 if ts else None, frac


async def run(args) -> dict:
    report: dict = {"host_os": platform.platform(), "python": platform.python_version(), "steps": {}}
    steps = report["steps"]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # 1. Discover, status, wake.
    device = RPDevice(args.host)
    status = await device.async_get_status()
    if not status:
        steps["1_status"] = {"ok": False, "error": "host unreachable"}
        return report
    users = device.get_users()
    user = args.user or (users[0] if users else "")
    report.update(
        host_type=device.host_type,
        system_version=device.system_version,
        status=device.status_name,
        registered_users=len(users),
    )
    if not user:
        steps["1_status"] = {"ok": False, "error": "no registered user; run --register first"}
        return report
    woke = False
    if not device.is_on:
        device.wakeup(user=user)
        woke = await device.async_wait_for_wakeup(timeout=60)
        if not woke:
            steps["1_status"] = {"ok": False, "error": "wakeup timed out"}
            return report
    steps["1_status"] = {"ok": True, "woke": woke, "app": device.app_name}

    # 2. Session 720p/30.
    rx = StampedReceiver()
    session = device.create_session(
        user, receiver=rx, resolution="720p", fps="low", loop=asyncio.get_running_loop()
    )
    if session is None or not await device.connect():
        steps["2_session"] = {"ok": False, "error": getattr(session, "error", "connect failed")}
        return report
    try:
        ready = await device.async_wait_for_session(timeout=20)
        first = await wait_frame_after(rx, 0, timeout=15) if ready else None
        steps["2_session"] = {"ok": bool(first), "ready": ready}
        if not first:
            return report
        device.controller.start()

        # 3. Save 10 frames + measure fps and decode CPU.
        cpu0, wall0, id0 = time.process_time(), time.monotonic(), rx.frame_id
        last, saved = 0, 0
        while saved < 10:
            got = await wait_frame_after(rx, last)
            if not got:
                break
            last, _, frame = got
            frame.to_image().save(out / f"frame_{saved:02d}.png")
            saved += 1
            await asyncio.sleep(0.2)
        wall = time.monotonic() - wall0
        report["fps"] = round((rx.frame_id - id0) / wall, 1)
        report["cpu_pct_one_core"] = round(100 * (time.process_time() - cpu0) / wall, 1)
        arr = frame.to_ndarray(format="rgb24")
        report["resolution"] = f"{arr.shape[1]}x{arr.shape[0]}"
        report["last_frame_mean_luma"] = round(float(arr.mean()), 1)  # ~0 => black/blocked content
        steps["3_frames"] = {"ok": saved == 10, "saved": saved}

        # 4. D-pad right changes the frame.
        noise = await noise_floor(rx)
        threshold = max(0.002, noise * 1.5)
        report["change_threshold"] = round(threshold, 4)
        lat, frac = await press_and_detect(device, rx, "RIGHT", threshold)
        steps["4_dpad_right"] = {
            "ok": lat is not None,
            "latency_ms": lat and round(lat),
            "changed_fraction": round(frac, 4),
        }
        await asyncio.sleep(1.0)

        # 5. PS button (opens control center on PS5); circle backs out.
        lat_ps, frac_ps = await press_and_detect(device, rx, "PS", threshold)
        _, _, frame = rx.latest()
        frame.to_image().save(out / "after_ps.png")
        steps["5_ps_button"] = {"ok": lat_ps is not None, "changed_fraction": round(frac_ps, 4)}
        await asyncio.sleep(1.0)
        await device.controller.async_button("CIRCLE", "tap", delay=0.08)
        await asyncio.sleep(1.5)

        # 6. Latency: 10 alternating RIGHT/LEFT taps.
        samples = []
        for i in range(10):
            lat, _ = await press_and_detect(device, rx, "RIGHT" if i % 2 == 0 else "LEFT", threshold)
            if lat is not None:
                samples.append(lat)
            await asyncio.sleep(0.8)
        steps["6_latency"] = {
            "ok": len(samples) >= 5,
            "samples_ms": [round(s) for s in samples],
            "median_ms": round(statistics.median(samples)) if samples else None,
        }
    finally:
        for button in ("RIGHT", "LEFT", "PS", "CIRCLE"):
            device.controller.button(button, "release")
        device.controller.stop()
        device.disconnect()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--host", default=os.environ.get("PS5_HOST"), required="PS5_HOST" not in os.environ)
    parser.add_argument("--user", default=os.environ.get("PS5_USER"))
    parser.add_argument("--out", default="spike_out")
    args = parser.parse_args()
    asyncio.run(run_and_save(args))


async def run_and_save(args) -> None:
    report = await run(args)
    gate = all(
        report["steps"].get(k, {}).get("ok") for k in ("1_status", "2_session", "3_frames", "4_dpad_right")
    )
    report["gate_passed"] = gate
    text = json.dumps(report, indent=2, ensure_ascii=False)
    Path(args.out, "report.json").write_text(text, encoding="utf-8")
    print(text, flush=True)
    # pyremoteplay leaves a pending UDP overlapped op; IocpProactor.close() then hangs forever on Windows.
    os._exit(0 if gate else 1)


if __name__ == "__main__":
    main()
