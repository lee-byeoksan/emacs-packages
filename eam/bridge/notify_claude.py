"""Forward only explicit Claude hook event names to terminal notifications.

No context, approval decisions, shell execution, files, network, or model calls.
Oversized/malformed input is ignored so notification failure cannot block work.
"""
import json
import sys


def notification(raw):
    if len(raw) > 1048576:
        return None
    try:
        event = json.loads(raw)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(event, dict):
        return None
    kind = event.get("hook_event_name")
    if kind not in ("Stop", "Notification", "PermissionRequest"):
        return None
    return {"terminalSequence": f"\x1b]777;notify;Claude Code;{kind}\x07"}


if __name__ == "__main__":
    result = notification(sys.stdin.buffer.read(1048577))
    if result:
        print(json.dumps(result))
