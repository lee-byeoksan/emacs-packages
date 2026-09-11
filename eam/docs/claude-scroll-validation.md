# Claude terminal scroll investigation (2026-09-17)

## Live GUI observations

The user reproduced severe trackpad scroll stutter in an existing Claude quick
session. Read-only inspection found Ghostel `semi-char`, mouse tracking enabled,
alternate screen enabled, a running display client, roughly 5 KiB / 61 buffer
lines, and a 16 ms redraw delay. This was not a frozen copy-mode session.
Other visible Codex sessions used the main screen without mouse tracking.
Claude wheel gestures therefore travel to the CLI and cause application redraws;
Codex's observed mode scrolls materialized terminal history instead.

Temporary function timing advice collected no input/output content and removed
itself automatically after 60 seconds. During user scrolling:

| Function | Calls | Total seconds | Maximum seconds |
|---|---:|---:|---:|
| Wheel forwarding | 9175 | 0.232 | 0.000096 |
| PTY output filter | 9430 | 0.172 | 0.000062 |
| Redraw attempt | 1357 | 2.078 | 0.055779 |

These are elapsed function times, not end-to-end latency or presented frames.
Redraw attempts include deferred/no-op attempts. Timing overhead, CLI rendering,
Emacs redisplay, GC outside these functions, transport latency and OS scheduling
are not isolated. The data does not establish a single exclusive bottleneck.

## Confirmed integration defect and correction

Ghostel resolves TERM=xterm-ghostty plus its bundled TERMINFO, TERM_PROGRAM and
COLORTERM, but the EAM daemon previously forced TERM=xterm-256color. The CLI was
started before the Ghostel display client, so it did not receive the display's
resolved terminal environment. Session startup now passes Ghostel's environment
to the manager/daemon; native PTY startup preserves TERM with an xterm-256color
fallback for unset/empty/dumb values. Ghostel's own terminfo fallback remains in
charge. This affects newly started CLI processes; attaching an existing process
does not replace its environment.

The PTY regression test starts a compiled fake CLI and checks the environment
actually received behind the daemon. It does not establish a GUI performance
improvement. The user's current session and personal settings were not changed.
A fresh Claude session with the corrected package needs a before/after GUI
comparison before claiming that stutter is fixed. Do not increase scrollback
capacity as a substitute for measuring this rendering path.

## Follow-up: intermittent stutter, window size and disconnect

The user reported that the terminal-environment correction felt substantially
better, but remained slower than a regular terminal. A later small-window run
felt normal; expanding the window made scrolling stutter. These are user
observations, not controlled before/after frame-latency measurements.

Temporary, automatically removed advice measured wheel forwarding, encoded
mouse calls, output filtering, native redraw success/refusal, Lisp redraw work,
and a 10 ms Emacs heartbeat. Only counts, byte sizes and durations were saved
under `var/scroll-analysis/`; no conversation contents were captured.

| 60-second capture | Initially smooth | Wide window (213 columns × 61 rows) |
|---|---:|---:|
| Wheel handler calls | 4,312 | 2,348 |
| Encoded mouse calls | 3,233 | 1,469 |
| Output received by filter | 2,220,060 B | 604,361 B |
| Successful native renders | 595 | 30 |
| Native render refusals | 323 | 2,084 |
| Native successful render total / maximum | 0.199 s / 1.24 ms | 0.013 s / 1.50 ms |
| Lisp redraw attempts total / maximum | 1.338 s / 52.51 ms | 3.629 s / 4.95 ms |
| Heartbeat maximum excess delay | 68.08 ms | 90.79 ms |
| GC count / total elapsed | 18 / 0.908 s | 4 / 0.207 s |

The wide run added nested timing for window anchoring: 2,131 calls took 3.238 s
(maximum 3.72 ms). Timing is inclusive: nested totals must not be added together.
In the inspected Ghostel renderer, synchronized output can refuse rendering;
its Lisp caller still performs cursor/window/post-render work. This identifies
avoidable work during deferred frames. It does not establish that all refusals
were caused by disconnect, or that anchoring caused the connection loss.

The wide run ended in `disconnected` during rapid scrolling. Inspection found:

- Native daemon and CLI still running; zero attached clients; ordinary session.
- Attach client exited with code 0; EAM recording error was nil.
- Daemon had observed 3,632,625 output bytes in the session, all within the
  5 MiB replay cap. This is a session lifetime total, not the capture interval.
- No disconnect reason was logged. The current daemon drops an attached client
  when its 256 KiB live-output queue overflows; attach treats socket EOF as a
  normal end. This is consistent with the observed state, but without a reason
  counter it is not proof of the exact disconnect branch.

An incomplete synchronized-output frame after a dropped connection could also
explain subsequent deferred redraws. Therefore the ordering “render slowness →
queue overflow” remains a hypothesis; aggregate timing cannot distinguish it
from “queue overflow → incomplete frame → repeated redraw attempts”. A later
inspection found synchronized-output mode off and no pending redraw/timer.

A separate compiled echo fixture compared a direct PTY with a PTY through EAM
attach and daemon (debug binary, no Emacs renderer). Three rounds of 500 measured
one-byte round trips after 50 warmups gave EAM median 0.027–0.047 ms and p95
0.039–0.058 ms, versus direct median 0.007–0.013 ms. This excludes a large fixed
idle transport delay under those conditions; it does not measure burst output,
Claude processing or GUI latency. This short independent probe ran during the
first GUI capture and is a possible scheduling confound.

Next investigations: timestamped queue occupancy and explicit disconnect
reasons; bounded backpressure/grace for brief GUI stalls rather than immediate
disconnect; skipping unnecessary post-render work on refused renders. Preserve
memory limits and responsive stop/control requests. Do not merely enlarge
scrollback or disable synchronized output as a presumed fix. No performance
settings, dependency sources, running CLI or personal init were changed in this
measurement. All timing advice and arming timers were removed/cancelled. Actual
large-window A/B improvement is still unverified.


## Implemented follow-up: bounded flow control and early render deferral

The live output queue remains bounded at 256KiB. The daemon reserves a full
16KiB read plus packet header before reading the PTY. When an attached reader
falls behind it pauses PTY reads, keeping control and input processing active;
detach resumes consumption. Replay and Ghostel scrollback remain 5MiB each.
Status now exposes queue occupancy/high-water, backpressure time/count and
explicit client disconnect reasons; final diagnostics survive in session.json.
An attached CLI can block on writing while its consumer is stalled. This is
intentional flow control, not an unlimited output buffer. The pre-existing
one-second final drain deadline after CLI exit is unchanged.

EAM registers a buffer-local Ghostel inhibit-redraw hook. While synchronized
output mode 2026 is open it defers before window anchoring and other render
postprocessing. Explicit forced redraws bypass the hook. Closing the frame
allows normal rendering again. No Ghostel source or provider-specific scroll
input behavior is changed. This avoids work on unfinished frames, but does not
reduce the cost of completed large-screen renders.

Automated validation uses a compiled fake CLI: pause the socket reader during
a 32MiB burst, verify bounded queue and retained attachment, then consume every
byte on the same connection and exchange Korean input. Separate cases detach
or stop while blocked. ERT checks that 100 deferred redraws do not enter window
lookup, that forced redraw and frame completion proceed, and that the real
Ghostel module's 2026 state is recognized. Existing lifecycle, resize, replay,
quick session and feature tests also run. GUI large-window Claude A/B latency
remains unverified; these tests establish flow-control and scheduling behavior,
not a measured improvement in user-perceived scrolling.
