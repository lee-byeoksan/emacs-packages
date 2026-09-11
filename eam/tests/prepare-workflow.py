#!/usr/bin/env python3
"""Create a fresh, offline fixture for the explicit real-CLI workflow check."""
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('directory', type=Path)
args = parser.parse_args()
root = args.directory.resolve()
root.mkdir(parents=True, exist_ok=False)
for provider in ('claude', 'codex'):
    project = root / provider / 'workspace'
    project.mkdir(parents=True)
    (root / provider / 'other-project').mkdir()
    (project / 'label.py').write_text('def label(text):\n    return text\n')
    (project / 'test_label.py').write_text('''import unittest
from label import label

class LabelTests(unittest.TestCase):
    def test_korean(self):
        self.assertEqual(label("  한글   프로젝트 "), "한글 프로젝트")
    def test_multiline(self):
        self.assertEqual(label("alpha\\n\\tbeta"), "alpha beta")
    def test_emoji(self):
        self.assertEqual(label("  코드🙂  "), "코드🙂")
    def test_empty(self):
        with self.assertRaises(ValueError):
            label(" \\t\\n")

if __name__ == '__main__':
    unittest.main()
''')
    (project / 'wait_probe.py').write_text('''import os, time
from pathlib import Path
Path("wait.pid").write_text(str(os.getpid()))
for _ in range(180):
    Path("heartbeat.txt").write_text(str(time.monotonic()))
    time.sleep(1)
Path("wait.finished").write_text("finished")
''')
(root / 'implement.txt').write_text(
    'label.py만 수정하세요. 문자열의 모든 연속 공백을 공백 한 칸으로 합치고 '
    '앞뒤 공백을 없애며, 결과가 비면 ValueError를 발생시켜야 합니다. '
    'test_label.py는 변경하지 말고 python3 -m unittest -v로 검증하세요. '
    '대화 확인 단어는 은하-924이며 파일에는 저장하지 마세요. '
    '테스트가 끝나면 요약하고 멈추세요. 다른 작업은 하지 마세요.\n')
(root / 'wait.txt').write_text(
    '중단 검증을 위해 python3 -u wait_probe.py만 실행하세요. '
    '파일을 수정하거나 다른 도구를 실행하지 말고 완료될 때까지 기다리세요.\n')
(root / 'resume.txt').write_text(
    '이전 대화의 확인 단어를 답하고, label 함수에 문자열이 아닌 입력이면 '
    'TypeError를 발생시키는 처리를 추가하세요. test_label.py에는 None과 정수 '
    '입력 테스트 두 개를 추가하고 python3 -m unittest -v를 실행하세요. '
    '확인 단어는 파일에 저장하지 마세요. 완료 후 멈추세요.\n')
print(root)
