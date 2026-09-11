import unittest
from events import Notifications, MAX_OSC, MAX_DCS, encode

class EventTests(unittest.TestCase):
    def test_fragmentation_utf8_offsets_and_order(self):
        data=b'ordinary'+b'\x1b]9;'+ '완료'.encode()+b'\x07\x1b]777;notify;Claude;stop\x1b\\'
        for size in range(1,len(data)+1):
            events=[]; parser=Notifications(events.append)
            for start in range(0,len(data),size): parser.feed(data[start:start+size])
            self.assertEqual([e['body'] for e in events],['완료','stop'])
            self.assertEqual([e['seq'] for e in events],[1,2])
            self.assertEqual(events[-1]['end_offset'],len(data))
            self.assertTrue(all(len(encode(e)) < 32768 for e in events))
    def test_bounded_invalid_and_nested_sequences(self):
        events=[]; p=Notifications(events.append)
        p.feed(b'\x1b]9;')
        for _ in range(1000):
            p.feed(b'x'*1000)
            self.assertLessEqual(len(p.pending),MAX_OSC)
        p.feed(b'\x07')
        p.feed(b'\x1b]9;\xff\x07')
        p.feed(b'\x1bP\x1b]9;fake\x07\x1b\\')
        p.feed(b'\x1b]0;title\x07')
        p.feed(b'\x1b]9;4;1;75\x07\x1b]9;9;/tmp\x07\x1b]9;\x07')
        p.feed(b'\x1b]9;cancel\x18')
        p.feed(b'\x1b]9;bad\x1b]9;nested\x07')
        self.assertEqual(events,[])
        p.feed(b'\x1b]9;valid\x07')
        self.assertEqual([e['body'] for e in events],['valid'])
    def test_writer_failure_propagates(self):
        def fail(_): raise OSError('disk full')
        p=Notifications(fail)
        with self.assertRaises(OSError): p.feed(b'\x1b]9;notice\x07')

    def test_oversized_dcs_is_bounded_and_recovers(self):
        events=[];p=Notifications(events.append)
        p.feed(b'\x1bPignored;')
        for _ in range(100):
            p.feed(b'x'*1000)
            self.assertLessEqual(len(p.pending),MAX_DCS)
        p.feed(b'\x1b\\\x1b]9;valid\x07')
        self.assertEqual([e['body'] for e in events],['valid'])

if __name__=='__main__': unittest.main()
