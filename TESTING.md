# Testing record

## Automated checks

On macOS with Python 3.12, `python -m unittest discover -s tests -v` passes ten tests. The tests cover packet framing, version rejection, layout validation, a three-screen handoff, the server/client loopback exchange, keyboard modifier encoding, mouse button and wheel forwarding, text clipboard chunks, disconnecting a client whose last keepalive reply is already more than nine seconds old, and suppressing an immediate reverse handoff after entry. `python -m compileall -q src tests` passes.

## Unmodified Barrier probe

On 24 October 2026, the official Barrier 2.4.0 `barrierc` binary from the project's release image connected without crypto to this server on localhost. Its log confirmed Barrier protocol 1.6 hello/hello-back, `QINF` → `DINF` reporting 1440×900, `CROP`, `CIAK`, and `CALV` with a `CALV` reply. In a follow-up run, it also logged receipt of `CBYE` and disconnection. The official process then exited with signal 11; the cause was not established. This probe verifies only that initial exchange and close path, not input or clipboard behavior with Barrier.

## Desktop access and remaining checks

On 25 October 2026, two macOS machines connected on the same LAN. The M5 client reported a 1470×956 screen; moving the primary pointer to the right edge triggered `CINN` and the cursor appeared on the client. The server then logged rapid alternating handoffs between primary and client, so the physical test stopped before typing or clipboard checks. The subsequent entry-margin, stale-motion cleanup and short return-guard fix passes the automated tests but has **not yet been physically retested**.

In the original local probe, the macOS process reported `Accessibility=False` and `Input Monitoring=False`; the CLI exited with code 2 and a clear permission message. For the two-Mac test, the user-run server passed preflight and listened, and the M5 client connected after its Accessibility permission was enabled.

Stable two-machine cursor movement, real keyboard/click/scroll injection, two-way clipboard, a full nine-second silent-client timeout, and screensaver behavior have not been verified. See the manual checklist in [README.md](README.md#manual-verification). Screensaver control is not implemented.
