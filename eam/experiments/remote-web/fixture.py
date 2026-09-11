#!/usr/bin/env python3
import os
import sys
print('\033[36mEAM remote test · 한글 입력 / approval / burst / size / exit\033[0m', flush=True)
for line in sys.stdin:
    line = line.strip()
    if line == 'exit':
        break
    if line == 'approval':
        print('Approve file edit? [y/n]', flush=True)
    elif line == 'size':
        print(f'SIZE {os.get_terminal_size()}', flush=True)
    elif line == 'burst':
        for i in range(12000):
            print(f'{i:05d} 한글 streaming output ' + 'x'*80)
        print('BURST-DONE', flush=True)
    else:
        print('REPLY: '+line, flush=True)
