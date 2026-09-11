#!/bin/bash
set -eu
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
python3 "$project_dir/scripts/prepare-gui-test.py"
exec /usr/bin/open -a "$project_dir/var/EAM Test.app"
