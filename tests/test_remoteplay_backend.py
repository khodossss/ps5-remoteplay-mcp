"""RemotePlayBackend input paths against a stub of the vendored library's device/session/controller."""

from types import SimpleNamespace

import pytest

from ps5agent.backend import Button, ConsoleError, Side
from ps5agent.backend.remoteplay import RemotePlayBackend
from ps5agent.config import Config


class StubController:
    def __init__(self) -> None:
        self.sent: list[tuple] = []
        self.fail = False

    def button(self, name: str, action: str) -> None:
        if self.fail:
            raise AttributeError("'NoneType' object has no attribute 'append'")
        self.sent.append(("button", name, action))

    def stick(self, side: str, point) -> None:
        self.sent.append(("stick", side, tuple(point)))

    def update_sticks(self) -> None:
        self.sent.append(("update_sticks",))

    def _add_event_buffer(self, event) -> None:
        pass

    def _send_event(self) -> None:
        self.sent.append(("event",))

    def stop(self) -> None:
        pass


class StubDevice:
    def __init__(self) -> None:
        self.transport = SimpleNamespace(closing=False)
        self.transport.is_closing = lambda: self.transport.closing
        stream = SimpleNamespace(_protocol=SimpleNamespace(transport=self.transport))
        self.session = SimpleNamespace(is_ready=True, is_stopped=False, stream=stream)
        self.controller = StubController()

    def disconnect(self) -> None:
        self.session = None


@pytest.fixture
def backend(tmp_path) -> RemotePlayBackend:
    b = RemotePlayBackend(Config(config_dir=tmp_path))
    b.device = StubDevice()
    return b


async def test_release_all_releases_only_what_was_pressed(backend):
    await backend.set_buttons({Button.CROSS})
    backend.device.controller.sent.clear()
    await backend.release_all()
    buttons = [s for s in backend.device.controller.sent if s[0] == "button"]
    assert buttons == [("button", "CROSS", "release")]
    assert ("update_sticks",) in backend.device.controller.sent  # centering sent now, not by a thread


async def test_failed_send_drops_the_session(backend):
    backend.device.controller.fail = True
    with pytest.raises(ConsoleError, match="session lost"):
        await backend.set_buttons({Button.CROSS})
    assert backend.device.session is None and backend._pressed == set()
    assert not backend._session_up


async def test_closed_stream_counts_as_no_session(backend):
    backend.device.transport.closing = True
    assert not backend._session_up
    with pytest.raises(ConsoleError):
        await backend.set_stick(Side.LEFT, 0.5, 0.0)
    assert backend.device.controller.sent == []
