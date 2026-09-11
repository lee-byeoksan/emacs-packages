"""No AI, no mouse mode, no alternate screen. q exits; any other key echoes."""
import os
import tty

tty.setraw(0)
for i in range(500):
    os.write(1, ('\x1b[36mROW %04d\x1b[0m 한글 코드·긴 로그 def example(): return %d\r\n' % (i, i)).encode())
os.write(1, b'READY: q quits; other input echoes\r\n')
while True:
    data = os.read(0, 4096)
    if data == b'q':
        break
    os.write(1, data)
