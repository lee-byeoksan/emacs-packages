"""Bounded extractor for explicit OSC 9 / OSC 777 notifications.

Does not modify the PTY stream or classify ordinary output. Oversized, invalid,
unterminated, or arbitrary nested control strings cannot create a notification.
Control strings other than explicit OSC notifications are ignored.
"""
import json

MAX_OSC = 8192
MAX_DCS = 2 * MAX_OSC + 8


class Notifications:
    def __init__(self, emit):
        self.emit = emit
        self.state = 'text'
        self.pending = bytearray()
        self.offset = 0
        self.sequence = 0

    def finish(self):
        try:
            text = self.pending.decode('utf-8', errors='strict')
        except UnicodeDecodeError:
            return
        if text.startswith(('9;4;', '9;9;')):
            return  # ConEmu progress / working directory, not a notification.
        if text.startswith('9;'):
            title, body = '', text[2:]
        elif text.startswith('777;notify;'):
            fields = text.split(';', 3)
            if len(fields) != 4:
                return
            title, body = fields[2:]
        else:
            return
        if not title and not body:
            return
        # Reject embedded controls; a notification cannot contain another OSC.
        if any(ord(c) < 32 or 127 <= ord(c) < 160 for c in title+body):
            return
        self.sequence += 1
        self.emit(dict(version=1, seq=self.sequence, end_offset=self.offset,
                       title=title, body=body))

    def feed(self, chunk):
        for byte in chunk:
            self.offset += 1
            state = self.state
            if state == 'text':
                if byte == 27:
                    self.state = 'escape'
            elif state == 'escape':
                if byte == 93:
                    self.pending.clear()
                    self.state = 'osc'
                elif byte in (80, 88, 94, 95):  # DCS, SOS, PM, APC
                    self.state = 'string'
                else:
                    self.state = 'escape' if byte == 27 else 'text'
            elif state in ('osc', 'discard'):
                if byte in (24, 26):
                    self.pending.clear()
                    self.state = 'text'
                elif byte == 7:
                    if state == 'osc':
                        self.finish()
                    self.pending.clear()
                    self.state = 'text'
                elif byte == 27:
                    self.state = 'osc-escape' if state == 'osc' else 'discard-escape'
                elif state == 'osc':
                    if len(self.pending) >= MAX_OSC:
                        self.pending.clear()
                        self.state = 'discard'
                    else:
                        self.pending.append(byte)
            elif state in ('osc-escape', 'discard-escape'):
                if byte == 92:
                    if state == 'osc-escape':
                        self.finish()
                    self.pending.clear()
                    self.state = 'text'
                else:
                    # Malformed/nested sequence: discard through its terminator.
                    self.pending.clear()
                    self.state = 'discard-escape' if byte == 27 else 'discard'
            elif state == 'string':
                if byte in (24, 26):
                    self.state = 'text'
                elif byte == 27:
                    self.state = 'string-escape'
            elif state == 'string-escape':
                self.state = 'text' if byte == 92 else ('string-escape' if byte == 27 else 'string')


def encode(event):
    return (json.dumps(event, ensure_ascii=False, separators=(',', ':'))+'\n').encode('utf-8')
