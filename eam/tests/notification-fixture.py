"""Emit fragmented OSC events on a real PTY; no AI, network, or input requests."""
import os
import sys
import time

label = sys.argv[1]
time.sleep(0.2)
for packet in [f"\x1b]9;{label} 한글 완료\x07", f"\x1b]777;notify;{label};확인 요청\x1b\\"]:
    data = packet.encode()
    for i in range(0, len(data), 3):
        os.write(1, data[i:i + 3])
        time.sleep(0.005)
time.sleep(0.3)
