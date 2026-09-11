#!/bin/bash
set -eu
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_dir"
/Applications/Emacs.app/Contents/MacOS/Emacs --batch -Q -L lisp \
  -l tests/review-test.el -f ert-run-tests-batch-and-exit
if [ "${1:-}" = "--magit" ]; then
  /Applications/Emacs.app/Contents/MacOS/Emacs --batch -Q \
    -L var/deps/cond-let-review -L var/deps/llama-review \
    -L var/deps/transient-review/lisp -L var/deps/with-editor-review/lisp \
    -L var/deps/magit-review/lisp -L lisp \
    -l tests/review-magit-test.el -f ert-run-tests-batch-and-exit
fi
