#!/bin/bash
set -eu
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
export EAM_TEST_ROOT="$project_dir"
export CARGO_HOME="${CARGO_HOME:-$project_dir/var/cargo}"
export EAM_NATIVE_FIXTURE="$project_dir/var/native-target/debug/examples/fixture"
cargo build --locked --manifest-path "$project_dir/native/Cargo.toml" --target-dir "$project_dir/var/native-target" --examples
mkdir -p "$project_dir/var/packages"
output="$(mktemp -d "$project_dir/var/packages/native.XXXXXX")"
export EAM_PACKAGE_TAR="${1:-$(bash "$project_dir/scripts/build-package.sh" "$output")}"
emacs_binary="${EAM_EMACS:-/Applications/Emacs.app/Contents/MacOS/Emacs}"
if [ ! -x "$emacs_binary" ]; then emacs_binary="$(command -v emacs)"; fi
"$emacs_binary" --batch -Q -l "$project_dir/tests/native-package-install.el"
