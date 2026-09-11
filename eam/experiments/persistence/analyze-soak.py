"""Summarize completed soak measurements without making a leak-free claim."""
import json
from pathlib import Path
import sys

root=Path(sys.argv[1]);r=json.loads((root/'result.json').read_text());samples=r['samples']
assert r['duration_seconds']>=600 and len(samples)>=50
assert samples[-1]['elapsed']>=r['duration_seconds']-20
assert all(b[1] for s in samples for b in s['buffers'])
assert max(len(s['buffers']) for s in samples)==4
assert any(len(s['buffers'])==2 for s in samples)
assert len(samples[-1]['buffers'])==4
assert max(s['notifications'] for s in samples)<=100
assert max(b[0] for s in samples for b in s['buffers'])<=65536

def emacs_rss(sample):
    m=sample['memory']
    return next(p['rss_kib'] for p in m['processes'] if p['pid']==m['emacs_pid'])
first,last=samples[0],samples[-1]
connected=[s for s in samples if len(s['buffers'])==4]
late=[s for s in samples if s['elapsed']>=450]
latencies=sorted(s['interval_timer_lateness_ms'] for s in samples)
value=dict(duration_seconds=r['duration_seconds'],samples=len(samples),
           final_sample_seconds=last['elapsed'],last_sample_archive_bytes=last['archive_bytes'],
           final_archive_bytes=sum(p.stat().st_size for p in (root/'persistent').glob('*/output.ansi')),
           max_buffer_chars=max(b[0] for s in samples for b in s['buffers']),max_notices=max(s['notifications'] for s in samples),
           emacs_gc_rss_first_kib=emacs_rss(first),emacs_gc_rss_last_kib=emacs_rss(last),
           emacs_gc_rss_late_min_kib=min(map(emacs_rss,late)),emacs_gc_rss_late_max_kib=max(map(emacs_rss,late)),
           total_connected_rss_first_kib=first['memory']['total_rss_kib'],
           total_connected_rss_last_kib=last['memory']['total_rss_kib'],
           total_connected_rss_max_kib=max(s['memory']['total_rss_kib'] for s in connected),
           process_count_first=len(first['memory']['processes']),process_count_last=len(last['memory']['processes']),
           sample_interval_max_timer_lateness_ms=max(latencies),
           all_sessions_stop_recorded=all(json.loads(p.read_text()).get('stopped') is True for p in (root/'persistent').glob('*/session.json')),
           ai_calls=0,gui_test=False,leak_free_proven=False)
assert value['all_sessions_stop_recorded']
(root/'summary.json').write_text(json.dumps(value,indent=2)+'\n')
print(json.dumps(value,indent=2))
