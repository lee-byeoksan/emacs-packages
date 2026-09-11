#!/usr/bin/env python3
"""Verify streamed bytes without loading the full archive into memory."""
import hashlib
import json
from pathlib import Path
import statistics
import sys

directory = Path(sys.argv[1])
samples = json.loads((directory / "samples.json").read_text())
results = []
for final in samples[-1]["sessions"]:
    identity = final["session"]
    expected = json.loads((directory / str(identity) / "expected.json").read_text())
    raw = Path(final["raw_file"])
    with raw.open("rb") as source:
        # ghostel--spawn-pty runs this exact clear-screen prefix before exec.
        prefix = source.read(7)
        assert prefix == b"\x1b[H\x1b[2J", repr(prefix)
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    assert digest == expected["sha256"], "Output content mismatch"
    assert raw.stat().st_size == expected["bytes"] + 7, "Output byte count mismatch"
    receipts = {r["text"]: r["received_at"] for r in
                map(json.loads, (directory / str(identity) / "receipts.jsonl").read_text().splitlines())}
    latencies = []
    rows = [next(s for s in sample["sessions"] if s["session"] == identity) for sample in samples]
    for row in rows:
        assert row["output_undo_disabled"] and row["input_undo_enabled"]
        if row["alive"]:
            assert row["tag"] in receipts, "Input receipt missing"
            latencies.append(1000 * (receipts[row["tag"]] - row["sent_at"]))
    results.append({"session": identity, "raw_bytes": raw.stat().st_size,
                    "producer_sha256_matches": True, "startup_prefix_bytes": 7,
                    "max_output_chars": max(r["output_chars"] for r in rows),
                    "inputs_received": len(latencies),
                    "input_receipt_median_ms": statistics.median(latencies),
                    "input_receipt_max_ms": max(latencies)})
pages = json.loads((directory / "history-pages.json").read_text())
assert len(pages) == 16 and max(pages) <= 4096
summary = {"elapsed_s": samples[-1]["elapsed_s"], "samples": len(samples),
           "emacs_rss_first_kib": samples[0]["rss_kib"],
           "emacs_rss_final_kib": samples[-1]["rss_kib"],
           "emacs_rss_max_kib": max(s["rss_kib"] for s in samples),
           "history_pages_checked": len(pages), "history_max_chars": max(pages),
           "sessions": results}
footprint_file = directory / "footprint.jsonl"
if footprint_file.exists():
    footprints = [json.loads(line) for line in footprint_file.read_text().splitlines()]
    assert footprints, "No physical footprint samples"
    start_wall = samples[0]["sessions"][0]["sent_at"] - samples[0]["elapsed_s"]
    values = [row["physical_footprint_bytes"] for row in footprints]
    summary["physical_footprint"] = {
        "samples": len(values), "first_bytes": values[0], "last_bytes": values[-1],
        "min_bytes": min(values), "max_bytes": max(values),
        "approx_first_soak_elapsed_s": footprints[0]["sampled_at"] - start_wall,
        "approx_last_soak_elapsed_s": footprints[-1]["sampled_at"] - start_wall}
(directory / "analysis.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps(summary, indent=2))
