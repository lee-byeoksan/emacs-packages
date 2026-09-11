;;; eam-standalone.el --- Project-local standalone storage -*- lexical-binding: t; -*-

;; Loaded only by the standalone launchers, never by the package entry point.
(require 'eam)
(setq eam-directory (expand-file-name "var/" eam--resource-directory))

(provide 'eam-standalone)
;;; eam-standalone.el ends here
