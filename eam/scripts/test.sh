#!/bin/bash
set -eu
exec bash "$(dirname "$0")/test-native.sh" "$@"
