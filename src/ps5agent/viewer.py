"""Window showing the console picture to the human at the computer (graph `ps5-agent-bridge`, node #29).

A separate process reading the daemon's /frame: SDL wants its window on the main thread, the daemon's
SDL already serves the gamepad, and closing the window must not touch the session."""

from __future__ import annotations

import ctypes
import io
import os
import subprocess
import sys
import warnings

import httpx
from PIL import Image

from .config import Config

POLL_MS = 8  # wait before asking again when no new frame was decoded


def spawn_viewer() -> subprocess.Popen:
    """Open the window next to the daemon; its output goes where the daemon's goes."""
    # the daemon has no console on Windows: without CREATE_NO_WINDOW the child would pop one up
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(
        [sys.executable, "-m", "ps5agent", "view"], stdin=subprocess.DEVNULL, creationflags=flags
    )


def run(cfg: Config) -> None:
    """Show frames until the window is closed (Esc or the close button) or the daemon is gone."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # pysdl2 warns which SDL binary it picked
        import sdl2

    if sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO) != 0:
        sys.exit(f"error: no window: {sdl2.SDL_GetError().decode(errors='replace')}")
    sdl2.SDL_SetHint(sdl2.SDL_HINT_RENDER_SCALE_QUALITY, b"1")
    window = sdl2.SDL_CreateWindow(
        b"PS5 Remote Play",
        sdl2.SDL_WINDOWPOS_CENTERED,
        sdl2.SDL_WINDOWPOS_CENTERED,
        1280,
        720,
        sdl2.SDL_WINDOW_RESIZABLE,
    )
    renderer = sdl2.SDL_CreateRenderer(window, -1, 0)
    texture, size, frame_id = None, (0, 0), 0
    event = sdl2.SDL_Event()
    try:
        with httpx.Client(base_url=cfg.daemon_url, timeout=5.0) as http:
            while True:
                dirty = False
                while sdl2.SDL_PollEvent(ctypes.byref(event)):
                    if event.type == sdl2.SDL_QUIT or (
                        event.type == sdl2.SDL_KEYDOWN and event.key.keysym.sym == sdl2.SDLK_ESCAPE
                    ):
                        return
                    dirty |= event.type == sdl2.SDL_WINDOWEVENT  # resized, exposed: redraw
                try:
                    response = http.get("/frame", params={"after": frame_id})
                except httpx.TransportError:
                    return  # the daemon stopped: nothing left to show
                if response.status_code == 200:
                    frame_id = int(response.headers["x-frame-id"])
                    img = Image.open(io.BytesIO(response.content)).convert("RGB")
                    if img.size != size:
                        if texture:
                            sdl2.SDL_DestroyTexture(texture)
                        size = img.size
                        texture = sdl2.SDL_CreateTexture(
                            renderer, sdl2.SDL_PIXELFORMAT_RGB24, sdl2.SDL_TEXTUREACCESS_STREAMING, *size
                        )
                        sdl2.SDL_RenderSetLogicalSize(renderer, *size)  # letterbox on resize
                    sdl2.SDL_UpdateTexture(texture, None, img.tobytes(), size[0] * 3)
                    dirty = True
                if not dirty:
                    sdl2.SDL_Delay(POLL_MS)
                    continue
                sdl2.SDL_RenderClear(renderer)
                if texture:
                    sdl2.SDL_RenderCopy(renderer, texture, None, None)
                sdl2.SDL_RenderPresent(renderer)
    finally:
        if texture:
            sdl2.SDL_DestroyTexture(texture)
        sdl2.SDL_DestroyRenderer(renderer)
        sdl2.SDL_DestroyWindow(window)
        sdl2.SDL_Quit()
