#!/bin/bash
# Build the native-source package using standard shell tools, no Python.
set -eu
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
output_dir="${1:-$project_dir/var/packages}"
version="$(sed -n 's/^;; Version: //p' "$project_dir/lisp/eam.el")"
prefix="eam-$version"
mkdir -p "$output_dir"
output_dir="$(cd "$output_dir" && pwd)"
output="$output_dir/$prefix.tar"
if [ -e "$output" ]; then echo "Refusing to overwrite $output" >&2; exit 1; fi
stage="$(mktemp -d "${TMPDIR:-/tmp}/eam-package.XXXXXX")"
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/$prefix/native" "$stage/$prefix/docs"
cp "$project_dir"/lisp/*.el "$stage/$prefix/"
rm "$stage/$prefix/eam-standalone.el"
cp "$project_dir/native/Cargo.toml" "$project_dir/native/Cargo.lock" "$stage/$prefix/native/"
cp -R "$project_dir/native/src" "$project_dir/native/web" "$stage/$prefix/native/"
cp "$project_dir"/docs/*.md "$stage/$prefix/docs/"
cp "$project_dir/.elpaignore" "$stage/$prefix/"
printf '(define-package "eam" "%s" "Native AI CLI management" '\''((emacs "30.1") (ghostel "0.53.0")))\n' "$version" > "$stage/$prefix/eam-pkg.el"
COPYFILE_DISABLE=1 tar --format=ustar -cf "$output" -C "$stage" "$prefix"
printf '%s\n' "$output"
