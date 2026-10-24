# Testing record

## Automated checks

On macOS with Python 3.12, `python -m unittest discover -s tests -v` passes nine tests. The tests cover packet framing, version rejection, layout validation, a three-screen handoff, the server/client loopback exchange, keyboard modifier encoding, mouse button and wheel forwarding, text clipboard chunks, and disconnecting a client whose last keepalive reply is already more than nine seconds old. `python -m compileall -q src tests` passes.

## Unmodified Barrier probe

On 24 October 2026, the official Barrier 2.4.0 `barrierc` binary from the project's release image connected without crypto to this server on localhost. Its log confirmed Barrier protocol 1.6 hello/hello-back, `QINF` → `DINF` reporting 1440×900, `CROP`, `CIAK`, and `CALV` with a `CALV` reply. In a follow-up run, it also logged receipt of `CBYE` and disconnection. The official process then exited with signal 11; the cause was not established. This probe verifies only that initial exchange and close path, not input or clipboard behavior with Barrier.

## Desktop access and remaining checks

The macOS process used for testing reported `Accessibility=False` and `Input Monitoring=False`. A non-suppressing input listener started and stopped, but warned that it was not trusted to monitor input. The CLI now exits with a clear permission message before starting a server or client in that state. A server launch with missing permissions returned exit code 2 and the expected message.

A second physical machine, real cursor/key/click/scroll injection, two-way clipboard, two-instance usage across machines, a full nine-second silent-client timeout, and screensaver behavior have not been verified. See the manual checklist in [README.md](README.md#manual-verification). Screensaver control is not implemented.
