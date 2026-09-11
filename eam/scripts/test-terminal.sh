#!/bin/bash
# Kept as an entry point; the native suite includes app and real PTY coverage.
set -eu
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
exec bash "$project_dir/scripts/test-native.sh" "$@"
