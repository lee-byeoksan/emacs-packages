#!/usr/bin/env python3
"""Literal byte search with bounded memory, reporting one match in a snapshot."""
import json
import os
import sys

CHUNK = 65536

def search(path, needle, start=0):
    if not 1 <= len(needle) <= 4096 or start < 0:
        raise ValueError('Use 1..4096 UTF-8 bytes and a nonnegative offset')
    with open(path, 'rb') as stream:
        size = os.fstat(stream.fileno()).st_size
        position = min(start, size)
        stream.seek(position)
        tail = b''
        while position < size:
            block = stream.read(min(CHUNK, size - position))
            if not block:
                raise ValueError('Archive shortened during search')
            window = tail + block
            found = window.find(needle)
            if found >= 0:
                return {'offset': position - len(tail) + found, 'size': size}
            tail = window[-(len(needle)-1):] if len(needle) > 1 else b''
            position += len(block)
        return {'offset': None, 'size': size}

if __name__ == '__main__':
    try:
        result = search(sys.argv[1], sys.stdin.buffer.read(4097), int(sys.argv[2]))
    except (OSError, ValueError) as error:
        result = {'error': str(error)}
    print(json.dumps(result))
