#!/usr/bin/env python3
import errno
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('archives', Path(__file__).resolve().parent.parent / 'scripts/archives.py')
archives = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archives)

class ArchivesTest(unittest.TestCase):
    def test_source_change_aborts(self):
        with tempfile.TemporaryDirectory() as folder:
            source, dest = Path(folder)/'source', Path(folder)/'copy'
            source.write_bytes(b'original')
            sync = archives.os.fsync
            def changed(fd):
                with source.open('ab') as stream:
                    stream.write(b'new')
                sync(fd)
            with patch.object(archives.os, 'fsync', side_effect=changed):
                with self.assertRaises(ValueError):
                    archives.export(source, dest)
            self.assertFalse(dest.exists())
            self.assertEqual(source.read_bytes(), b'originalnew')

    def test_large_copy_bounded_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            source, dest = Path(folder)/'terminal-test.ansi', Path(folder)/'copy.ansi'
            with source.open('wb') as stream:
                for _ in range(24):
                    stream.write(b'x' * archives.CHUNK)
            tracemalloc.start()
            result = archives.export(source, dest)
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            self.assertLess(peak, 4 * archives.CHUNK)
            self.assertEqual(result['bytes'], 24 * archives.CHUNK)
            with source.open('rb') as stream:
                self.assertEqual(result['sha256'], hashlib.file_digest(stream, 'sha256').hexdigest())
            with self.assertRaises(FileExistsError):
                archives.export(source, dest)
            self.assertEqual(archives.inventory(folder, 0)['omitted'], 1)

    def test_sync_failure_does_not_publish_partial_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            source, dest = Path(folder)/'source', Path(folder)/'copy'
            source.write_bytes('한글🙂'.encode())
            with patch.object(archives.os, 'fsync', side_effect=OSError(errno.ENOSPC, 'fault-injected ENOSPC')):
                with self.assertRaises(OSError):
                    archives.export(source, dest)
            self.assertFalse(dest.exists())
            self.assertEqual(source.read_bytes(), '한글🙂'.encode())
            self.assertEqual(list(Path(folder).iterdir()), [source])

if __name__ == '__main__':
    unittest.main()
