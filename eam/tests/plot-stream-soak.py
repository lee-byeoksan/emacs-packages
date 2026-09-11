#!/usr/bin/env python3
"""Plot a completed, verified soak; requires matplotlib only for this artifact."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("directory", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
summary = json.loads((args.directory / "analysis.json").read_text())
assert all(row["producer_sha256_matches"] for row in summary["sessions"])
samples = json.loads((args.directory / "samples.json").read_text())
footprints = [json.loads(line) for line in (args.directory / "footprint.jsonl").read_text().splitlines()]
minutes = [row["elapsed_s"] / 60 for row in samples]
start_wall = samples[0]["sessions"][0]["sent_at"] - samples[0]["elapsed_s"]
fig, axes = plt.subplots(3, 1, figsize=(9, 8), sharex=True, constrained_layout=True)
axes[0].plot(minutes, [sum(s["raw_bytes"] for s in row["sessions"]) / 2**20
                       for row in samples], color="#2878b5")
axes[0].set_ylabel("Archive size (MiB)")
axes[0].set_ylim(bottom=0)
for identity, color in ((0, "#2878b5"), (1, "#db7d21")):
    axes[1].plot(minutes, [next(s["output_chars"] for s in row["sessions"] if s["session"] == identity) / 1000
                           for row in samples], label=f"Session {identity + 1}", color=color, linewidth=1)
axes[1].set_ylabel("Visible buffer (1000 chars)")
axes[1].set_ylim(bottom=0)
axes[1].legend(loc="lower right")
axes[2].plot(minutes, [row["rss_kib"] / 1024 for row in samples], label="RSS", color="#2878b5")
axes[2].plot([(row["sampled_at"] - start_wall) / 60 for row in footprints],
             [row["physical_footprint_bytes"] / 2**20 for row in footprints],
             label="Physical footprint (late sampling if applicable)", color="#278759")
axes[2].set_ylabel("Memory (MiB)")
axes[2].set_ylim(bottom=0)
axes[2].set_xlabel("Elapsed time (minutes)")
axes[2].legend(loc="lower right")
for axis in axes:
    axis.grid(alpha=.2)
fig.suptitle("Two fake CLI sessions in batch Emacs / Ghostel\nArchived bytes verified against producer SHA-256")
fig.savefig(args.output, dpi=160)
plt.close(fig)
