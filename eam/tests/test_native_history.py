"""Offline native schema, filtering, pagination and read-only regression tests."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridge/persistence'))
import history


class NativeHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def claude(self, items):
        path = self.root / 'projects' / 'project' / 'conversation.jsonl'
        path.parent.mkdir(parents=True)
        path.write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in items))
        return dict(provider='Claude', id='conversation', path=str(path))

    def test_claude_filtering_and_partial_tail(self):
        def item(role, content, **extra):
            return dict(type=role, sessionId='conversation', cwd=str(self.root),
                        message=dict(role=role, content=content), **extra)
        entry = self.claude([
            item('user', '한글 요청'),
            item('assistant', [dict(type='thinking', thinking='hidden'),
                               dict(type='tool_use', name='shell'), dict(type='text', text='```go\n코드\n```')]),
            item('user', [dict(type='tool_result', content='hidden')]),
            item('user', 'metadata', isMeta=True),
            item('assistant', 'subagent', isSidechain=True),
            dict(type='assistant', sessionId='other', message=dict(role='assistant',content='other'))])
        with open(entry['path'], 'ab') as f:
            f.write(b'{"unfinished":')
        before = Path(entry['path']).read_bytes()
        self.assertEqual(list(history.messages(entry)), [('user','한글 요청'), ('assistant','```go\n코드\n```')])
        listing = history.catalog('Claude', self.root, str(self.root))
        self.assertEqual(listing['entries'][0]['title'], '한글 요청')
        self.assertEqual(before, Path(entry['path']).read_bytes())

    def test_pages_reconstruct_long_unicode_and_prompt(self):
        text = '한글 코드\n' * 30000
        entry = self.claude([dict(type='user', message=dict(role='user', content=text)),
                             dict(type='assistant', message=dict(role='assistant', content='끝'))])
        expected = '\n## 사용자\n\n' + text + '\n\n## 응답\n\n끝\n'
        chunks = []
        for offset in range(0,len(expected),4096):
            page = history.page(entry, offset,4096)
            self.assertLessEqual(len(page['text']),4096)
            chunks.append(page['text'])
        self.assertEqual(''.join(chunks),expected)
        self.assertEqual(history.page(entry,None,4096)['text'], expected[-4096:])
        self.assertEqual(history.page(entry,4096,4096,True)['text'], text[4096:8192])
        self.assertEqual(history.page(entry,None,4096,True)['text'], text[:4096])
        self.assertEqual(history.page(entry,len(expected)+10,4096)['start'],len(expected))

    def test_codex_sqlite_and_legacy_isolation(self):
        state = self.root / 'state_5.sqlite'
        db = self.root / 'thread_history_1.sqlite'
        with sqlite3.connect(state) as con:
            con.execute('CREATE TABLE threads (id,cwd,title,rollout_path,history_mode,updated_at)')
            con.execute('INSERT INTO threads VALUES (?,?,?,?,?,?)',('one',str(self.root),'제목','unused','paginated',10))
        with sqlite3.connect(db) as con:
            con.execute('CREATE TABLE thread_items (thread_id,rollout_ordinal,item_type,item_json)')
            for identity, order, kind, data in [
                ('one',1,'userMessage',dict(content=[dict(type='text',text='요청')])),
                ('one',2,'reasoning',dict(text='hidden')),
                ('other',3,'agentMessage',dict(text='other conversation')),
                ('one',4,'agentMessage',dict(text='응답\n두 줄'))]:
                con.execute('INSERT INTO thread_items VALUES (?,?,?,?)',(identity,order,kind,json.dumps(data)))
        before = {p:p.read_bytes() for p in (state,db)}
        entry = history.catalog('Codex',self.root,str(self.root))['entries'][0]
        self.assertEqual(list(history.messages(entry)),[('user','요청'),('assistant','응답\n두 줄')])
        self.assertEqual(history.catalog('Codex',self.root,'/other')['entries'],[])
        for p,data in before.items(): self.assertEqual(p.read_bytes(),data)
        path = self.root / 'legacy.jsonl'
        path.write_text(json.dumps(dict(type='response_item', payload=dict(type='message',role='assistant',content=[dict(type='output_text',text='legacy')]))))
        entry.update(mode='legacy',path=str(path))
        self.assertEqual(list(history.messages(entry)),[('assistant','legacy')])

    def test_bad_schema_and_oversized_records_fail(self):
        with self.assertRaises(sqlite3.Error): history.catalog('Codex',self.root)
        entry = self.claude([])
        Path(entry['path']).write_bytes(b' ' * (history.ROW_LIMIT+1))
        with self.assertRaises(ValueError): list(history.messages(entry))
        Path(entry['path']).write_bytes(b'{broken}\n')
        with self.assertRaises(ValueError): list(history.messages(entry))

if __name__ == '__main__': unittest.main()
