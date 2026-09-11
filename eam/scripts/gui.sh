#!/bin/bash
set -eu
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
# LaunchServices creates and activates a separate macOS application instance.
# Executing the inner binary directly can leave an unactivated process behind.
exec /usr/bin/open -n -a /Applications/Emacs.app --args \
  -Q --title 'eam — offline prototype' \
  -L "$project_dir/lisp" -l eam-standalone -l "$project_dir/tests/support/eam-demo.el" -f eam-demo-new
