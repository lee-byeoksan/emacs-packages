#!/usr/bin/env python3
"""Check relocated resources and storage defaults without GUI, network or AI."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
EMACS = "/Applications/Emacs.app/Contents/MacOS/Emacs"


def run(root, profile, standalone=False):
    quote = lambda p: json.dumps(str(p), ensure_ascii=False)
    entry = "eam-standalone" if standalone else "eam-app"
    expected = root / "var" if standalone else profile / "eam"
    form = f"""(progn
      (setq user-emacs-directory {quote(str(profile) + '/')})
      (require '{entry})
      (unless (equal (directory-file-name eam-directory) {quote(expected)})
        (error "Wrong data directory: %s" eam-directory))
      (when (file-exists-p eam-directory) (error "Created records on load"))
      (unless (equal (directory-file-name eam--resource-directory) {quote(root)})
        (error "Wrong installed resource root"))
      (unless (file-exists-p (expand-file-name "docs/user-guide.md" eam--resource-directory))
        (error "Missing manual"))
      (unless (equal (process-list) nil) (error "Started a process on load"))
      (message "Relocated %s load passed" '{entry}))"""
    subprocess.run([EMACS, "--batch", "-Q", "-L", str(root / "lisp"),
                    "-L", str(ROOT / "var/deps/ghostel/lisp"),
                    "--eval", form], check=True)


with tempfile.TemporaryDirectory(prefix="eam-package-paths-") as tmp:
    base = Path(tmp)
    installed = base / "설치 폴더"
    shutil.copytree(ROOT / "lisp", installed / "lisp",
                    ignore=shutil.ignore_patterns("*.elc"))
    (installed / "docs").mkdir()
    shutil.copyfile(ROOT / "docs/user-guide.md", installed / "docs/user-guide.md")
    run(installed, base / "profile")
    run(installed, base / "profile", standalone=True)
    assert not (base / "profile").exists(), "Modified profile while loading"
    assert not (installed / "var").exists(), "Modified install while loading"
    print("Both relocated loads passed; no profile, records, GUI or AI created")
