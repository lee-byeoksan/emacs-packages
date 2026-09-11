#!/usr/bin/env python3
"""Historical entry point. Prefer bash scripts/test-native-package.sh [TAR]."""
from pathlib import Path
import subprocess
import sys
root = Path(__file__).resolve().parents[1]
args = [a for a in sys.argv[1:] if not a.startswith("--")]
raise SystemExit(subprocess.call(["bash", str(root / "scripts/test-native-package.sh"), *args]))
