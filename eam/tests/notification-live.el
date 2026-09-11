;;; notification-live.el --- Explicit authenticated notification probe -*- lexical-binding: t; -*-
;; Not part of offline tests. Starts one CLI and submits one short request.
(require 'eam-app)
(let* ((provider (getenv "EMACS_AI_NOTICE_PROVIDER"))
       (eam-directory (file-name-as-directory (getenv "EMACS_AI_NOTICE_DIR")))
       (workspace (expand-file-name default-directory))
       (prompt (or (getenv "EMACS_AI_NOTICE_PROMPT")
                   "Reply exactly NOTICE_PROBE_DONE. Do not use tools or modify any files."))
       (args (if (equal provider "codex")
                 (list "-c" "tui.notifications=true" "-c" "tui.notification_method=\"osc9\""
                       "-c" "tui.notification_condition=\"always\""
                       "--sandbox" "read-only" "--ask-for-approval" "on-request"
                       "-c" "approvals_reviewer=\"user\"" prompt)
               (list "--permission-mode" "manual" "--settings"
                     (getenv "EMACS_AI_NOTICE_SETTINGS") prompt)))
       (eam-notification-desktop nil)
       (session (eam-terminal-start provider (eam--executable provider) args workspace))
       (deadline (+ (float-time) 45)))
  (unwind-protect
      (progn
        (set-frame-size (selected-frame) 120 40)
        (while (and (< (float-time) deadline) (not eam-notifications--entries)
                    (process-live-p (eam-terminal-process session)))
          (accept-process-output nil .1)
          (with-current-buffer (eam-terminal-output session)
            (ghostel--redraw-now (current-buffer) t)
            (write-region (point-min) (point-max)
                          (expand-file-name "screen.txt" eam-directory) nil 'silent)))
        (with-temp-file (expand-file-name "result.json" eam-directory)
          (insert (json-encode
                   `((provider . ,provider)
                     (process . ,(process-status (eam-terminal-process session)))
                     (raw_file . ,(eam-terminal-file session))
                     (events . ,(vconcat
                                 (mapcar (lambda (entry)
                                           `((title . ,(eam-notice-title entry))
                                             (body . ,(eam-notice-body entry))))
                                         eam-notifications--entries))))))))
    (with-current-buffer (eam-terminal-output session) (eam-detach))
    (server-force-delete)))
