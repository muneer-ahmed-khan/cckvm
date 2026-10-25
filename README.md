# Software KVM (Coding Challenge #142)

`cckvm` is a Python implementation of the Barrier 1.6 wire protocol. It includes a TCP server and a client, directional screen layouts, cursor handoff, keyboard and mouse commands, keepalives, and text clipboard transfer. The code targets a single primary macOS display and uses `pynput` for desktop input plus `pyperclip` for clipboard access.

**Status:** automated protocol and loopback tests pass. An unmodified Barrier client completed the initial hello, screen-info and keepalive exchange on localhost. Two-Mac cursor tests reached the client but exposed rapid back-and-forth handoffs. The macOS capture adapter now filters its own injected cursor moves and lets its cursor warp pass through the event tap; this fix awaits a physical retest. Treat this as an experimental build until the [manual checklist](#manual-verification) is complete. In particular, real keyboard/click/scroll/clipboard behavior, full genuine Barrier interoperability, screensaver notifications, and multi-display coordinate behavior remain unverified. The client currently logs `CSEC` but does not activate its local screensaver; the server does not detect screensaver changes. TLS and file transfer are not implemented. See [the testing record](TESTING.md).

## Install

Use Python 3.10–3.12 on each Mac:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
cckvm --help
```

Grant the terminal app running `cckvm` macOS **Accessibility** and **Input Monitoring** access in System Settings → Privacy & Security. Restart the terminal after granting access. Barrier must be configured for a plain TCP connection because this build does not implement TLS. Use it only on a trusted network; the connection has no authentication or encryption.

The CLI checks these permissions at startup and exits with a specific message when they are missing. The server needs both; the client needs Accessibility for input injection.

## Configure and run

Copy [`config.example.json`](config.example.json) and give each screen the exact name its client uses. Every direction is explicit; define a reverse link if movement should work both ways. `primary` names the machine with the physical keyboard and mouse. The example has three screens in an L shape.

On the primary Mac:

```sh
cckvm server --config config.example.json --host 0.0.0.0 --port 24800
```

On another Mac, using the screen name from the configuration:

```sh
cckvm client --name office-pc --host PRIMARY_MAC_IP --port 24800
```

Run another client with `--name laptop` for the third screen. Press Ctrl+C to close cleanly. To test against an unmodified Barrier client, configure it with the exact screen name and point it to the primary Mac on port 24800 with SSL disabled. To test this client against Barrier, give the Barrier server a matching screen entry and point `cckvm client` to it.

While controlling a client, press **Control + Option + Escape** to return control to the primary Mac immediately. This shortcut is handled locally by the server and is useful if an edge handoff does not return as expected.

## Automated checks

```sh
python -m unittest discover -s tests -v
```

The eleven tests use loopback TCP sockets and fake desktop adapters. They exercise the handshake, incompatible-version rejection, screen information, a handoff and return guard, mouse, keyboard and clipboard commands, a three-screen layout, timeout enforcement, two instances of this implementation, and the macOS event-tap filtering rule. They do not establish full Barrier interoperability or verify OS-level capture/injection.

## Manual verification

1. Connect an unmodified Barrier client. Confirm its configured name and resolution appear in server logs, and that the cursor enters and returns across the configured edge.
2. Type ordinary text, Shift+A, held keys, and common shortcuts on the client; click every mouse button and scroll in both directions. Check that the primary machine does not also receive those events while the client is active.
3. Copy short Unicode text in each direction. Leave the connection idle for more than 12 seconds, then check it remains active. Stop the server and confirm the client exits.
4. Repeat with a Barrier server and this client, then with two `cckvm` instances on separate machines. Add a third client and traverse the L-shaped layout.

The protocol encodings were checked against Barrier's [message definitions](https://github.com/debauchee/barrier/blob/master/src/lib/barrier/protocol_types.cpp), [version and message documentation](https://github.com/debauchee/barrier/blob/master/src/lib/barrier/protocol_types.h), [key IDs](https://github.com/debauchee/barrier/blob/master/src/lib/barrier/key_types.h), and [clipboard archive](https://github.com/debauchee/barrier/blob/master/src/lib/barrier/IClipboard.cpp). This is an independent implementation and is not affiliated with Barrier.
