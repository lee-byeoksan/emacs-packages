#!/usr/bin/env python3
"""Inspect archive sizes and export a stable file without overwriting or deleting."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

ROOT = Path(__file__).resolve().parent.parent
CHUNK = 1024 * 1024

def inventory(directory, limit=50):
    rows, total, count = [], 0, 0
    with os.scandir(directory) as entries:
        for entry in entries:
            if not entry.is_file(follow_symlinks=False):
                continue
            if not ((entry.name.startswith('terminal-') and
                     entry.name.endswith(('.ansi', '.ansi.input.jsonl'))) or
                    (entry.name.startswith('session-') and entry.name.endswith('.txt'))):
                continue
            info = entry.stat(follow_symlinks=False)
            count += 1
            total += info.st_size
            if len(rows) < limit:
                rows.append({'name': entry.name, 'bytes': info.st_size})
    return {'directory': str(directory), 'files': count, 'bytes': total,
            'shown': rows, 'omitted': count - len(rows), 'recursive': False}

def identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

def export(source, destination):
    """Publish only a verified stable copy; errors remove only our staging file."""
    source, destination = Path(source), Path(destination)
    if source.is_symlink() or not stat.S_ISREG(source.stat().st_mode):
        raise ValueError('Choose a regular archive file, not a symlink')
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    fd, staging = tempfile.mkstemp(prefix='.eam-export-', dir=destination.parent)
    try:
        digest = hashlib.sha256()
        with os.fdopen(os.open(source, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as incoming, os.fdopen(fd, 'wb') as outgoing:
            fd = None
            if not stat.S_ISREG(os.fstat(incoming.fileno()).st_mode):
                raise ValueError('Choose a regular archive file')
            before = identity(os.fstat(incoming.fileno()))
            remaining = before[2]
            while remaining:
                block = incoming.read(min(CHUNK, remaining))
                if not block:
                    raise ValueError('Archive shortened during export')
                outgoing.write(block)
                digest.update(block)
                remaining -= len(block)
            outgoing.flush()
            os.fsync(outgoing.fileno())
            if before != identity(os.fstat(incoming.fileno())) or before != identity(source.stat()):
                raise ValueError('Archive changed during export; stop the CLI and retry')
        with open(staging, 'rb') as copied:
            actual = hashlib.file_digest(copied, 'sha256').hexdigest()
        if actual != digest.hexdigest():
            raise ValueError('Export checksum mismatch')
        # Unlike rename/replace, link refuses a destination that appeared meanwhile.
        os.link(staging, destination)
        return {'source': str(source), 'destination': str(destination),
                'bytes': before[2], 'sha256': actual, 'original_retained': True}
    finally:
        if fd is not None:
            os.close(fd)
        os.unlink(staging)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    listing = commands.add_parser('list')
    listing.add_argument('--directory', type=Path, default=ROOT / 'var')
    listing.add_argument('--limit', type=int, default=50)
    copying = commands.add_parser('export')
    copying.add_argument('source', type=Path)
    copying.add_argument('destination', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'list':
            if not 0 <= args.limit <= 1000:
                parser.error('limit must be between 0 and 1000')
            result = inventory(args.directory, args.limit)
        else:
            result = export(args.source, args.destination)
    except (OSError, ValueError) as error:
        parser.exit(1, f'{error}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
