;;; terminal-gui.el -*- lexical-binding: t; -*-
(require 'eam-terminal)
(defconst eam-terminal-gui-root
  (expand-file-name ".." (file-name-directory (or load-file-name buffer-file-name))))
(defvar eam-terminal-gui-session nil)
(defun eam-terminal-gui-workdir ()
  (let ((dir (expand-file-name "var/terminal-workspace" eam-terminal-gui-root)))
    (make-directory dir t) dir))
(defun eam-terminal-gui-fixture ()
  (interactive)
  (setq eam-terminal-gui-session
        (eam-terminal-start "Ghostel fixture" (eam--executable "python3")
                                 (list (expand-file-name "tests/terminal-fixture.py" eam-terminal-gui-root)
                                       (expand-file-name "fixture-received.jsonl" (eam-terminal-gui-workdir)))
                                 (eam-terminal-gui-workdir))))
(defun eam-terminal-gui-claude ()
  (interactive)
  (setq eam-terminal-gui-session
        (eam-terminal-start-provider "Claude" (eam-terminal-gui-workdir))))
(defun eam-terminal-gui-codex ()
  (interactive)
  (setq eam-terminal-gui-session
        (eam-terminal-start-provider "Codex" (eam-terminal-gui-workdir))))
(defun eam-terminal-gui-sample (text)
  (with-current-buffer (eam-terminal--ensure-draft eam-terminal-gui-session)
    (erase-buffer) (insert text))
  (pop-to-buffer (eam-terminal--ensure-draft eam-terminal-gui-session)))
(defun eam-terminal-gui-korean () (interactive) (eam-terminal-gui-sample "한글🙂 첫 줄\n두 번째 줄"))
(defun eam-terminal-gui-model () (interactive) (eam-terminal-gui-sample "/model"))
(defun eam-terminal-gui-goal () (interactive) (eam-terminal-gui-sample "/goal"))
(defun eam-terminal-gui-copy-mode ()
  (interactive)
  (pop-to-buffer (eam-terminal-output eam-terminal-gui-session))
  (ghostel-copy-mode))
(defun eam-terminal-gui-input-mode ()
  (interactive)
  (pop-to-buffer (eam-terminal-output eam-terminal-gui-session))
  (ghostel-semi-char-mode))
(defun eam-terminal-gui-capture ()
  (interactive)
  (let ((s eam-terminal-gui-session))
    (with-current-buffer (eam-terminal-output s)
      (let ((coding-system-for-write 'utf-8-unix))
        (write-region (point-min) (point-max)
                      (expand-file-name (concat (replace-regexp-in-string "[^A-Za-z0-9]" "_" (buffer-name)) ".screen.txt")
                                        (eam-terminal-gui-workdir)) nil 'silent)))))
(easy-menu-define eam-terminal-test-menu global-map "Terminal experiment fixtures."
  '("Terminal Test"
    ["Start fake CLI" eam-terminal-gui-fixture t]
    ["Start Claude CLI" eam-terminal-gui-claude t]
    ["Start Codex CLI" eam-terminal-gui-codex t]
    "--" ["Draft Korean sample" eam-terminal-gui-korean t]
    ["Draft /model" eam-terminal-gui-model t]
    ["Draft /goal" eam-terminal-gui-goal t]
    ["Copy mode" eam-terminal-gui-copy-mode t]
    ["Return to terminal input" eam-terminal-gui-input-mode t]
    ["Capture terminal screen" eam-terminal-gui-capture t]))
