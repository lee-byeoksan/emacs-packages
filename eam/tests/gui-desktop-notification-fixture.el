;;; gui-desktop-notification-fixture.el --- Explicit optional banner probe -*- lexical-binding: t; -*-

;; Load into an isolated GUI; loading this file sends no notification.
(require 'eam-app)
(defvar eam-test-desktop--root
  (file-name-directory (directory-file-name
                        (file-name-directory (or load-file-name buffer-file-name)))))

(defun eam-test-desktop-notification ()
  "Send one synthetic notice through the real optional alert backend.
Backend return is recorded separately from visual OS banner verification."
  (interactive)
  (let* ((load-path (cons (expand-file-name "var/deps/alert" eam-test-desktop--root)
                          load-path))
         (origin (get-buffer-create "*AI desktop probe*"))
         (evidence (expand-file-name "var/desktop-notification-probe.json"
                                     eam-test-desktop--root))
         (called nil) (returned nil) (failure nil)
         (observer (lambda (fn &rest args)
                     (setq called t)
                     (condition-case err
                         (prog1 (apply fn args) (setq returned t))
                       (error (setq failure (error-message-string err))
                              (signal (car err) (cdr err)))))))
    (require 'alert)
    (advice-add 'alert-osx-notifier-notify :around observer)
    (unwind-protect
        (let ((eam-notification-desktop t)
              (alert-default-style 'osx-notifier)
              (alert-user-configuration nil)
              (alert-log-messages nil))
          (eam-notifications--receive
           origin "EAM test"
           (format "Synthetic notification / 한글 확인 / %s" (format-time-string "%H:%M:%S"))))
      (advice-remove 'alert-osx-notifier-notify observer)
      (with-temp-file evidence
        (insert (json-encode
                 `((backend . "osx-notifier")
                   (backend_called . ,(if called t :json-false))
                   (backend_returned . ,(if returned t :json-false))
                   (error . ,failure)
                   (visual_banner_verified . :json-false)
                   (ai_requests . 0)))))
      (eam-notifications))))

;;; gui-desktop-notification-fixture.el ends here
