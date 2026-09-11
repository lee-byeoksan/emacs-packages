#!/bin/bash
# Open an isolated source profile; no Python or personal init files.
set -eu
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
ghostel_dir="${EAM_GHOSTEL_LISP:-$project_dir/var/deps/ghostel/lisp}"
if [ ! -f "$ghostel_dir/ghostel.el" ]; then
  echo "Ghostel is missing. Set EAM_GHOSTEL_LISP to its lisp directory." >&2
  exit 1
fi
exec /usr/bin/open -n -a /Applications/Emacs.app --args -Q \
  --chdir "$project_dir" -L "$ghostel_dir" -L "$project_dir/lisp" \
  -l eam-standalone -l eam-app -f eam-keys-mode
