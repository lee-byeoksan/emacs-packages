;;; eam-vc-init.el --- Install EAM from the package repository -*- lexical-binding: t; -*-
;; Requires Emacs 30.1 and an available Ghostel dependency.
;; Use after the eam/ repository layout has been pushed to the remote.
;; For an existing old-URL install, reinstall EAM explicitly; :vc does not migrate it.
(require 'use-package)
(use-package eam
  :vc (:url "git@github.com:lee-byeoksan/emacs-packages.git"
       :branch "main"
       :lisp-dir "eam/lisp"
       :rev :newest)
  :demand t
  :init
  (setq eam-directory (expand-file-name "~/.eam/")
        eam-native-auto-build t          ; first use: Rust/Cargo + C build tools
        eam-terminal-redraw-delay 0.016) ; seconds: 16ms (default)
  :config
  (require 'eam-app)
  (eam-keys-mode 1))
;;; eam-vc-init.el ends here
