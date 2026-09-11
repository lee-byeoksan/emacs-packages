#!/usr/bin/env python3
"""Install the local tar into a new isolated profile. Does not launch a GUI."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[1]
EMACS = "/Applications/Emacs.app/Contents/MacOS/Emacs"
VERSION = re.search(r"^;; Version: ([0-9]+(?:\.[0-9]+)+)$",
                    (ROOT / "lisp/eam.el").read_text(), re.M)[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--package", type=Path, default=ROOT / f"var/packages/eam-{VERSION}.tar")
parser.add_argument("--profile", type=Path, default=ROOT / f"var/package-profile-eam-{VERSION}")
args = parser.parse_args()
artifact = args.package.resolve(strict=True)
dependency = ROOT / "var/deps/ghostel"
module = dependency / "ghostel-module.dylib"
if not module.is_file() or hashlib.sha256(module.read_bytes()).hexdigest() != \
        "6e4a509c23fe6c39e90610dd17cb2543622436898f86daf80f47cde523240b9d":
    parser.error("Prepare the pinned dependency first: python3 scripts/setup-ghostel.py")
if not re.search(r"^;; Version: 0\.53\.0$", (dependency / "lisp/ghostel.el").read_text(), re.M):
    parser.error("Expected original Ghostel 0.53.0")
profile = args.profile.resolve()
profile.mkdir(parents=True, exist_ok=False)
quote = lambda value: json.dumps(str(value), ensure_ascii=False)
bootstrap = profile / "bootstrap.el"
bootstrap.write_text(f""";;; Isolated local test profile; no personal init is loaded.
(require 'package)
(setq user-emacs-directory {quote(str(profile) + '/')}
      package-user-dir {quote(profile / 'elpa')}
      package-archives nil
      package-quickstart nil)
(package-initialize t)
;; Reuse the pinned upstream checkout without copying or changing its sources.
(push (cons 'ghostel
            (list (package-desc-create :name 'ghostel :version '(0 53 0)
                    :dir {quote(dependency / 'lisp')} :kind 'tar)))
      package-alist)
(add-to-list 'load-path {quote(dependency / 'lisp')})
(require 'ghostel)
(push 'ghostel package-activated-list)
(package-activate-all)
""")
log = profile / "install.log"
with log.open("w") as stream:
    result = subprocess.run([EMACS, "--batch", "-Q", "-l", str(bootstrap), "--eval",
                             f"(package-install-file {quote(artifact)})"],
                            stdout=stream, stderr=subprocess.STDOUT)
if result.returncode:
    raise SystemExit(f"Install failed; inspect {log}. No GUI was launched.")
verify = f"""(progn
  (eam-keys-mode 1)
  (unless (file-in-directory-p eam--resource-directory package-user-dir)
    (error "Product code was not loaded from installed package"))
  (when (file-exists-p eam-directory) (error "Created records before use"))
  (when (process-list) (error "Started a process before use"))
  (dolist (name '("eam" "eam-app" "eam-terminal"
                  "eam-history" "eam-connect" "eam-caffeine" "eam-notifications" "eam-review" "eam-worktree" "eam-persistent" "eam-persistent-events"))
    (unless (file-exists-p (expand-file-name (concat name ".elc") eam--resource-directory))
      (error "Failed to compile %s" name)))
  (message "Installed profile ready; no CLI started"))"""
subprocess.run([EMACS, "--batch", "-Q", "-l", str(bootstrap), "--eval", verify], check=True)
launch = profile / "start.sh"
launch.write_text("#!/bin/bash\nset -eu\nexec " + shlex.join([
    "/usr/bin/open", "-n", "-a", "/Applications/Emacs.app", "--args", "-Q",
    "--title", "EAM package", "-l", str(bootstrap), "--eval", "(eam-keys-mode 1)"]) + "\n")
launch.chmod(0o755)
(profile / "artifact.json").write_text(json.dumps({
    "package": str(artifact), "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
    "ghostel": str(dependency), "records": str(profile / "eam"),
}, indent=2) + "\n")
print(f"Ready (GUI not launched). Run: bash {shlex.quote(str(launch))}")
