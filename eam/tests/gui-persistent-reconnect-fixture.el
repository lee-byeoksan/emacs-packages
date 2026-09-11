;;; gui-persistent-reconnect-fixture.el --- New GUI attachment fixture -*- lexical-binding: t; -*-
;; Lifecycle/rendering evidence only; not a keyboard command test.
(require 'eam-app)
(require 'eam-persistent)
(let* ((root (file-name-as-directory (getenv "EMACS_AI_PERSISTENT_FIXTURE")))
       (session (expand-file-name "persistent/session" root)))
  (setq eam-directory (expand-file-name "records/" root))
  (eam-keys-mode 1)
  (eam-attach session)
  (delete-other-windows)
  (run-at-time 900 nil (lambda () (eam-persistent--call "stop" `((session . ,session))))))
