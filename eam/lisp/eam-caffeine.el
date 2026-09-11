;;; eam-caffeine.el --- Attachment-aware macOS idle sleep prevention -*- lexical-binding: t; -*-
(require 'seq)
(require 'cl-lib)
(defvar eam-caffeine--process nil)
(defvar eam-caffeine-mode nil)
(defvar eam-caffeine--automatic-change nil)
(defvar eam-caffeine--auto-owned nil)
(defvar eam-caffeine--timer nil)
(defvar eam-caffeine--checking nil)

(defun eam-caffeine--sync (inventory)
  "Follow verified attachment state without overriding manual ownership."
  (let ((attached (seq-some (lambda (entry) (> (or (alist-get 'attached_clients entry) 0) 0))
                            (alist-get 'sessions inventory)))
        (eam-caffeine--automatic-change t))
    (cond ((and attached (not eam-caffeine-mode))
           (eam-caffeine-mode 1)
           (setq eam-caffeine--auto-owned t))
          ((and (not attached) (not (alist-get 'unverified inventory))
                eam-caffeine--auto-owned)
           (eam-caffeine-mode -1)
           (setq eam-caffeine--auto-owned nil)))))

(defun eam-caffeine--check ()
  "Inspect attachment metadata only, without model/usage polling."
  (when (and (eq system-type 'darwin) (not eam-caffeine--checking)
             (fboundp 'eam-app--live-inventory))
    (let ((eam-caffeine--checking t) (eam-native-auto-build nil))
      (condition-case nil
          (eam-caffeine--sync (eam-app--live-inventory))
        (error nil)))))

(defun eam-caffeine--watch ()
  "Follow attached sessions in interactive Emacs. Native clients own assertions too."
  (unless (or noninteractive eam-caffeine--timer (not (eq system-type 'darwin)))
    (setq eam-caffeine--timer (run-at-time 1 5 #'eam-caffeine--check))))

(defun eam-caffeine--stop ()
  "Release only the assertion process started by this Emacs."
  (when (process-live-p eam-caffeine--process)
    (delete-process eam-caffeine--process))
  (setq eam-caffeine--process nil))

;;;###autoload
(define-minor-mode eam-caffeine-mode
  "Manually prevent macOS idle system and display sleep while Emacs lives.
Attached sessions enable it automatically; manual enable stays on after detach."
  :global t :init-value nil :lighter " Caffeine"
  :group 'eam
  (unless eam-caffeine--automatic-change (setq eam-caffeine--auto-owned nil))
  (if eam-caffeine-mode
      (condition-case err
          (progn
            (unless (and (eq system-type 'darwin) (file-executable-p "/usr/bin/caffeinate"))
              (user-error "Caffeine requires macOS /usr/bin/caffeinate"))
            (unless (process-live-p eam-caffeine--process)
              (setq eam-caffeine--process
                    (make-process
                     :name "eam-caffeine" :buffer nil :noquery t
                     :command (list "/usr/bin/caffeinate" "-d" "-i" "-w" (number-to-string (emacs-pid)))
                     :sentinel
                     (lambda (process _event)
                       (when (and (eq process eam-caffeine--process)
                                  (not (process-live-p process)))
                         (setq eam-caffeine--process nil eam-caffeine-mode nil)
                         (force-mode-line-update t))))))
            (add-hook 'kill-emacs-hook #'eam-caffeine--stop))
        (error
         (setq eam-caffeine-mode nil)
         (eam-caffeine--stop)
         (signal (car err) (cdr err))))
    (remove-hook 'kill-emacs-hook #'eam-caffeine--stop)
    (eam-caffeine--stop))
  (force-mode-line-update t))

(provide 'eam-caffeine)
