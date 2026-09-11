;;; ghostel-probe.el --- Opt-in standalone PTY fake CLI probe -*- lexical-binding: t; -*-
;; Loading this file does not start processes or change personal settings.
(require 'eam-terminal)
(defconst eam-pty-probe--directory
  (file-name-directory (or load-file-name buffer-file-name)))
(defvar eam-pty-probe-runtime nil)
(defun eam-pty-probe-attach (runtime)
  "Attach Ghostel to the experimental daemon in RUNTIME."
  (interactive (list (or eam-pty-probe-runtime
                         (read-directory-name "Experiment runtime: "))))
  (let ((buffer (generate-new-buffer "*EAM PTY experiment*")))
    (with-current-buffer buffer
      (let ((ghostel-use-native-pty nil)
            (ghostel-shell-integration nil)
            (ghostel-module-auto-install nil)
            (ghostel-max-scrollback (* 128 1024))
            (ghostel-timer-delay eam-terminal-redraw-delay))
        (ghostel-exec buffer (executable-find "python3")
                      (list (expand-file-name "relay.py" eam-pty-probe--directory)
                            "attach" runtime)))
      (setq-local ghostel-timer-delay eam-terminal-redraw-delay)
      (setq-local header-line-format
                  "PTY experiment | wheel: Ghostel scroll | q: exit fake CLI | kill buffer: detach")
      (buffer-disable-undo))
    (pop-to-buffer buffer)
    buffer))
(defun eam-pty-probe-new ()
  "Start only the fake CLI in a separate PTY owner, then attach Ghostel."
  (interactive)
  (let* ((runtime (make-temp-file "/tmp/eam-pty-" t))
         (process-environment (cons "TERM=xterm-256color" process-environment)))
    (with-temp-buffer
      (unless (zerop (process-file
                      (executable-find "python3") nil t nil
                      (expand-file-name "relay.py" eam-pty-probe--directory)
                      "start" runtime "--" (executable-find "python3") "-u"
                      (expand-file-name "fixture.py" eam-pty-probe--directory)))
        (error "PTY experiment failed: %s" (buffer-string))))
    (setq eam-pty-probe-runtime runtime)
    (eam-pty-probe-attach runtime)))
(provide 'eam-pty-probe)
