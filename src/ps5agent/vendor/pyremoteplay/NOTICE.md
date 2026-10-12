# Vendored pyremoteplay

This directory contains a modified copy of [pyremoteplay](https://github.com/ktnrg45/pyremoteplay)
0.7.6 by ktnrg45, licensed under the GNU General Public License v3 (see `LICENSE` here).

Only the protocol modules are included (no GUI, gamepad or CLI). Changes made for ps5-remoteplay-mcp,
2026-10-12:

- Absolute `pyremoteplay.*` imports turned into relative imports.
- `ddp.py`: interface enumeration uses `psutil` instead of `netifaces` (no Windows wheels).
- `session.py`: `ExecutorEventEmitter` imported from `pyee.executor` (pyee >= 9); fixed the inverted
  loop condition in `wait()`, `standby()` and `async_standby()` that made them return immediately.
- `receiver/__init__.py`: codec flags work with both old (`LOW_DELAY`) and new (`low_delay`) PyAV names.
- `stream.py`: the network-test BIG message sends client version 9 (PS5 rejects 7), as chiaki-ng does.
- `stream.py`: sends from other threads (controller worker, AV and I/O executors) run on the event loop:
  `send_data`, `_send_data_ack`, `send_feedback` and `send_congestion` (they advance cipher and TSN
  state), `Protocol.sendto` and `Protocol.close`; a closed transport is skipped. Cross-thread sends
  killed the stream on Windows.
- `av.py`: forward error correction uses `ps5agent.fec` (pure numpy) instead of the `pyjerasure`
  C extension.
- `device.py`: removed the PlayStation Store cover-art lookup (dropped the `pyps4-2ndscreen` dependency).
