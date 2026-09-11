;;; gui-notifications-fixture.el --- Finite GUI notification fixture -*- lexical-binding: t; -*-
(require 'eam-app)
(let* ((root (file-name-as-directory (getenv "EMACS_AI_NOTICE_ROOT")))
       (eam-directory (expand-file-name "records/" root))
       (fixture (expand-file-name "notification-fixture.py"
                                  (file-name-directory load-file-name))))
  (when (file-exists-p root) (error "Use a new fixture directory"))
  (make-directory root t)
  (setq eam-notification-desktop nil)
  (dolist (label '("Alpha" "Beta"))
    (eam-terminal-start label (executable-find "python3")
                             (list fixture label) root))
  (eam-keys-mode 1)
  (run-at-time 2 nil
               (lambda ()
                 (eam-notifications)
                 (delete-other-windows)
                 (goto-char (point-min)))))
