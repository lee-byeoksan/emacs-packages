#!/bin/bash
set -eu
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
python3 "$project_dir/scripts/setup-ghostel.py"
python3 "$project_dir/scripts/prepare-gui-test.py" --terminal
exec /usr/bin/open -n -a "$project_dir/var/EAM Terminal Test.app"
