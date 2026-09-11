;;; eam-init.el --- Load EAM source into an existing Emacs -*- lexical-binding: t; -*-
;; Evaluate this file or copy its forms into your own configuration.
;; No GUI or CLI is started by loading it.
(require 'use-package)
(use-package eam
  :ensure nil
  :load-path "~/workspace/emacs-ai/eam/lisp"
  :demand t
  :init
  (setq eam-directory (expand-file-name "~/.eam/")
        eam-quick-directory (expand-file-name "~/.eam/quick/") ; C-c a s
        eam-native-auto-build t          ; first use: Rust/Cargo + C build tools
        eam-terminal-redraw-delay 0.016) ; seconds: 16ms (default)
  :config
  (require 'eam-app)
  (eam-keys-mode 1))
;;; eam-init.el ends here
