#!/bin/bash
# All helpers and fake CLIs in this suite are compiled binaries, never Python.
set -eu
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
export CARGO_HOME="${CARGO_HOME:-$project_dir/var/cargo}"
export EAM_TEST_ROOT="$project_dir"
target="$project_dir/var/native-target"
cargo build --locked --manifest-path "$project_dir/native/Cargo.toml" --target-dir "$target" --bins --examples
cargo test --locked --manifest-path "$project_dir/native/Cargo.toml" --target-dir "$target" -- --test-threads=1
export EAM_NATIVE_BIN="$target/debug/eam-runtime"
export EAM_NATIVE_FIXTURE="$target/debug/examples/fixture"
emacs_binary="${EAM_EMACS:-/Applications/Emacs.app/Contents/MacOS/Emacs}"
if [ ! -x "$emacs_binary" ]; then emacs_binary="$(command -v emacs)"; fi
"$emacs_binary" --batch -Q -L "$project_dir/lisp" \
  --eval '(setq eam-native-executable (getenv "EAM_NATIVE_BIN") load-prefer-newer t)' \
  -l "$project_dir/tests/native-build-test.el" \
  -l "$project_dir/tests/app-test.el" \
  -l "$project_dir/tests/session-list-test.el" \
  -l "$project_dir/tests/usage-test.el" \
  -l "$project_dir/tests/session-observation-test.el" \
  -l "$project_dir/tests/caffeine-test.el" \
  -l "$project_dir/tests/native-history-test.el" \
  -l "$project_dir/tests/history-test.el" \
  -l "$project_dir/tests/notifications-test.el" \
  -l "$project_dir/tests/review-test.el" \
  -l "$project_dir/tests/worktree-test.el" \
  -l "$project_dir/tests/pty-backend-test.el" \
  -l "$project_dir/tests/terminal-display-test.el" \
  -f ert-run-tests-batch-and-exit
