;;; eam-persistent.el --- Explicit persistent terminal sessions -*- lexical-binding: t; -*-
(require 'eam-worktree)
(require 'eam-native)
(require 'eam-usage)
(defconst eam-persistent--code
  (expand-file-name "native/" eam--resource-directory))
(require 'eam-persistent-events)
(defvar eam-persistent--readers (make-hash-table :test #'equal))
(defvar-local eam-persistent--timer nil)
(defvar-local eam-persistent--display-state nil)

(defun eam-persistent--call (action request)
  "Invoke only an explicit local management operation."
  (with-temp-buffer
    (insert (json-encode request))
    (let ((status (call-process-region
                   (point-min) (point-max) (eam-native--executable)
                   t t nil "manager" action)))
      (unless (eq status 0) (error "%s" (buffer-string)))
      (goto-char (point-min))
      (json-parse-buffer :object-type 'alist :array-type 'list :null-object nil))))

(defun eam-persistent--cancel ()
  (eam-usage--cancel)
  (when eam-persistent--timer
    (cancel-timer eam-persistent--timer)
    (setq eam-persistent--timer nil)))

(defun eam-persistent--poll (buffer reader)
  (if (not (buffer-live-p buffer)) nil
    (with-current-buffer buffer
      (if (not (process-live-p (eam-terminal-process eam-terminal--current)))
          (progn
            (setq eam-persistent--display-state
                  (condition-case nil
                      (let* ((info (eam-persistent--call
                                    "inspect" `((session . ,eam-terminal-persistent-directory))))
                             (state (alist-get 'status info)))
                        (if (equal (alist-get 'state state) "stopped")
                            (format "terminated%s"
                                    (let ((code (alist-get 'exit_code state)))
                                      (if (and code (not (equal code "")))
                                          (format "(%s)" code) "")))
                          (if (equal (alist-get 'state state) "interrupted")
                              "interrupted" "disconnected")))
                    (error "disconnected (unverified)")))
            (eam-persistent--cancel)
            (unless (or (string-prefix-p "terminated" eam-persistent--display-state)
                        (equal eam-persistent--display-state "interrupted"))
              (setq eam-persistent--timer
                    (run-at-time 1 1 #'eam-persistent--poll buffer reader)))
            (setq header-line-format
                  (concat "PERSISTENT | " eam-persistent--display-state
                          (if (equal eam-persistent--display-state "interrupted")
                              " | C-c a l 목록에서 복구 / C-c a h 기록"
                            " | C-c a h 기록 / C-c a d 분리")))
            (when (string-prefix-p "terminated" eam-persistent--display-state)
              (let* ((session eam-terminal--current)
                     (draft (eam-terminal-input session))
                     (directory eam-terminal-persistent-directory))
                ;; Automatic cleanup must not discard unsaved user text.
                (when (and (buffer-live-p draft) (buffer-modified-p draft))
                  (setf (eam-terminal-input session) nil)
                  (message "CLI ended; unsaved draft retained in %s" (buffer-name draft)))
                (remhash directory eam-persistent--readers)
                (eam-detach))))
        (condition-case err (eai-event-reader-poll reader)
          (error (eam-persistent--cancel)
                 (message "Persistent events stopped: %s" (error-message-string err))))))))

(defun eam-persistent--wait-attachment (session directory token)
  "Confirm this display's PID, not another client's endpoint, within four seconds."
  (let ((file (expand-file-name "editor.json" directory))
        (process (eam-terminal-process session))
        (deadline (+ (float-time) 4)) ready)
    (while (and (not ready) (process-live-p process) (< (float-time) deadline))
      (let ((attrs (file-attributes file)))
        (when (and attrs (null (file-attribute-type attrs))
                   (<= (file-attribute-size attrs) 4096))
          (condition-case nil
              (let ((endpoint (with-temp-buffer
                                (insert-file-contents file nil 0 4097)
                                (json-parse-buffer :object-type 'alist :false-object nil))))
                (setq ready (and (eq (alist-get 'connected endpoint) t)
                                 (equal (alist-get 'attachment_pid endpoint) (process-id process))
                                 (equal (alist-get 'attachment_token endpoint) token))))
            (file-error nil))))
      (unless ready (accept-process-output process .025)))
    (unless (and ready (process-live-p process))
      (user-error "Persistent display connection failed or timed out; CLI was not restarted"))))

;;;###autoload
(defun eam-attach (&optional directory)
  "Select a detached CLI to attach.  DIRECTORY is for programmatic callers.
The running CLI is never restarted."
  (interactive)
  (if (null directory)
      (progn (require 'eam-app) (eam-sessions t))
    (eam-persistent--attach directory)))

(defun eam-persistent--attach (directory)
  "Attach to the existing CLI stored in DIRECTORY."
  (setq directory (expand-file-name directory))
  (let* ((info (eam-persistent--call "inspect" `((session . ,directory))))
         (metadata (alist-get 'metadata info))
         (state (alist-get 'status info))
         (reuse (seq-some
                 (lambda (buffer)
                   (with-current-buffer buffer
                     (when (and (equal eam-terminal-persistent-directory directory)
                                eam-terminal--current
                                (eq buffer (eam-terminal-output eam-terminal--current))
                                (not (process-live-p (eam-terminal-process eam-terminal--current))))
                       eam-terminal--current)))
                 (buffer-list))))
    (unless (and (equal (alist-get 'state state) "running")
                 (zerop (alist-get 'attached_clients state)))
      (user-error "Session is not running or is already attached"))
    (eam-terminal--editor-command)
    (when reuse
      (with-current-buffer (eam-terminal-output reuse)
        (eam-persistent--cancel)))
    (let ((configuration (current-window-configuration))
          (token (secure-hash 'sha256 (prin1-to-string (list (emacs-pid) (current-time) (random)))))
          session success)
      (unwind-protect
          (progn
            (setq session
                  (eam-terminal-start
                   (alist-get 'provider metadata) (eam-native--executable)
                   (list "attach" directory
                         (eam-terminal--emacsclient)
                         (expand-file-name server-name server-socket-dir) token)
                   (alist-get 'directory metadata) reuse))
            ;; Redraw is display data, never another write to the canonical log.
            (set-process-filter (eam-terminal-process session) #'ghostel--filter)
            (setf (eam-terminal-file session) (alist-get 'archive metadata))
            (eam-persistent--wait-attachment session directory token)
            (let* ((buffer (eam-terminal-output session))
                   (reader (or (gethash directory eam-persistent--readers)
                               (eai-event-reader-open (alist-get 'events metadata) buffer))))
              (with-current-buffer buffer
                (setq-local eam-terminal-persistent-directory directory)
                (setq-local eam-terminal-temporary (eq t (alist-get 'temporary metadata)))
                (setq-local eam-persistent--display-state nil)
                (setq-local ghostel-notification-function nil)
                (setq-local header-line-format
                            (format "PERSISTENT %s | M-x eam-detach / eam-quit"
                                    "PTY"))
                (add-hook 'kill-buffer-hook #'eam-persistent--cancel nil t)
                (setq eam-persistent--timer
                      (run-at-time 0 .1 #'eam-persistent--poll buffer reader)))
              ;; Commit reader and notice ownership after fallible setup.
              (let ((previous (eai-event-reader-buffer reader)))
                (dolist (notice eam-notifications--entries)
                  (when (eq previous (eam-notice-buffer notice))
                    (setf (eam-notice-buffer notice) buffer))))
              (setf (eai-event-reader-buffer reader) buffer)
              (puthash directory reader eam-persistent--readers))
            (eam-persistent--set-label session (alist-get 'name metadata))
            (setq success t)
            session)
        (unless success
          (when (and session (buffer-live-p (eam-terminal-output session)))
            (with-current-buffer (eam-terminal-output session)
              (eam-persistent--cancel)
              (if reuse
                  (progn
                    (eam-terminal--stop session)
                    (setq-local eam-terminal-persistent-directory directory)
                    (setq-local eam-terminal--current session))
                (eam-detach))))
          (set-window-configuration configuration))))))

(defun eam-persistent--set-label (session name)
  "Apply NAME to SESSION display buffers without changing provider or identity."
  (let ((name (or name "")))
    (with-current-buffer (eam-terminal-output session)
      (rename-buffer (format "*%s terminal: %s%s*"
                             (eam-terminal-name session)
                             (if (string-empty-p name) "" (concat name " | "))
                             (abbreviate-file-name (eam-terminal-directory session))) t)
      (eam-usage--install name))
    (when (buffer-live-p (eam-terminal-input session))
      (with-current-buffer (eam-terminal-input session)
        (rename-buffer (eam-terminal--draft-name session) t)))))

(defun eam-persistent--start (provider directory extra-args &optional name temporary recover-from)
  "Create a persistent CLI with EXTRA-ARGS for native conversation resume."
  (setq provider (eam--provider-name provider))
  (let* ((parent (expand-file-name "persistent" eam-directory))
         ;; The daemon starts before ghostel-exec starts the display client.
         ;; Give the CLI the same capabilities as its actual renderer.
         (process-environment
          (append (ghostel--terminal-env) (copy-sequence process-environment)))
         (directory (expand-file-name directory))
         (session (expand-file-name (format "%s-%s" (downcase provider)
                                           (format-time-string "%Y%m%d-%H%M%S-%N")) parent)))
    (make-directory parent t)
    (eam-persistent--call
     "start" `((session . ,session) (provider . ,provider)
               (backend . "pty") (name . ,(or name ""))
               (telemetry . t)
               (recover_from . ,recover-from)
               (resume_id . ,(when (and (= (length extra-args) 2)
                                       (member (car extra-args) '("resume" "--resume")))
                              (cadr extra-args)))
               (temporary . ,(if temporary t :json-false))
               (executable . ,(eam--executable (downcase provider)))
               (raw_recording . ,(if eam-record-terminal t :json-false))
               (args . ,(vconcat (append (eam-notifications-cli-args provider) extra-args)))
               (directory . ,directory)))
    ;; Startup is asynchronous. Wait only for recorder readiness, not AI output.
    (let ((deadline (+ (float-time) 3)))
      (while (and (not (file-exists-p (expand-file-name "events.jsonl" session)))
                  (< (float-time) deadline))
        (accept-process-output nil .05)))
    (condition-case err (eam-attach session)
      (error (message "Persistent session saved at %s" session)
             (signal (car err) (cdr err))))))

;;;###autoload
(defun eam-persist ()
  "Keep the selected quick session alive after detach, without restarting it.
The conversation, working directory, name and notes remain unchanged."
  (interactive)
  (let* ((directory (eam-session-context-directory))
         (metadata (eam-persistent--call "persist" `((session . ,directory)))))
    (unless (memq (alist-get 'temporary metadata) '(nil :false))
      (user-error "Daemon did not confirm persistence"))
    (dolist (buffer (buffer-list))
      (with-current-buffer buffer
        (when (and eam-terminal-persistent-directory
                   (equal (directory-file-name eam-terminal-persistent-directory)
                          (directory-file-name directory)))
          (setq-local eam-terminal-temporary nil))))
    (when (derived-mode-p 'eam-session-list-mode)
      (eam-session-list--refresh))
    (message "Session kept: detach now leaves the CLI running; eam-rename changes its name")))

;;;###autoload
(defun eam-quit (directory)
  "Explicitly stop the selected owned server, retaining its records."
  (interactive (list (eam-session-context-directory)))
  (when (yes-or-no-p (format "Stop persistent CLI in %s? " directory))
    (let ((result (eam-persistent--call
                   "stop" `((session . ,(expand-file-name directory))))))
      (unless (equal (alist-get 'state result) "stopped")
        (user-error "Stop could not be verified (%s); inspect with M-x eam-status"
                    (or (alist-get 'state result) "unknown")))
      (remhash (expand-file-name directory) eam-persistent--readers)
      (message "Persistent CLI server stopped; disk records retained"))))
(defun eam-session-context-directory ()
  "Resolve the selected row, CLI, draft or note to its EAM session."
  (or (and (fboundp 'eam-session-list--at-point)
           (alist-get 'session (eam-session-list--at-point)))
      eam-terminal-persistent-directory
      (bound-and-true-p eam-note--directory)
      (and eam-terminal--current
           (buffer-live-p (eam-terminal-output eam-terminal--current))
           (buffer-local-value 'eam-terminal-persistent-directory
                               (eam-terminal-output eam-terminal--current)))
      (user-error "Use a session list row, CLI, draft or note buffer")))
(defvar-local eam-status--info nil)
(defun eam-status--render ()
  (let* ((inhibit-read-only t) (v eam-usage--snapshot) (a (alist-get 'activity v))
         (position (point)))
    (erase-buffer)
    (insert (propertize "EAM session status\n\n" 'face 'bold)
            "Session: " eam-terminal-persistent-directory "\n"
            "Work: " (eam-usage--activity-text v) "\n"
            "State event: " (or (alist-get 'since a) "unknown") "\n"
            "Tools: " (if (seq-empty-p (alist-get 'tools a)) "—"
                        (mapconcat #'identity (alist-get 'tools a) ", ")) "\n\n"
            "Latest observed request (up to 1024 characters):\n"
            (let ((prompt (alist-get 'prompt a)))
              (if (and (stringp prompt) (not (string-empty-p prompt))) prompt
                "Unavailable in the bounded transcript tail")) "\n\n"
            (eam-usage--text) "\n" (or (eam-usage--provider-text) "") "\n"
            "Last local check: " (if eam-usage--checked-at
                                      (format-time-string "%F %T" eam-usage--checked-at) "pending") "\n\n"
            "Usage source time: " (format "%s" (or (alist-get 'timestamp v) "unknown")) "\n\n"
            "~ = inferred; tool calls may include permission waits.\n"
            "Transcript tail is limited to 1 MiB; missing data is unknown, not idle.\n\n"
            (pp-to-string eam-status--info))
    (goto-char (min position (point-max)))))
(defun eam-status--refresh ()
  (interactive)
  (setq eam-status--info (eam-persistent--call "inspect" `((session . ,eam-terminal-persistent-directory))))
  (eam-usage--refresh (current-buffer) t))
(defvar eam-status-mode-map
  (let ((map (make-sparse-keymap)))
    (define-key map (kbd "g") #'eam-status--refresh)
    (define-key map (kbd "j") #'eam-note)
    map))
(define-derived-mode eam-status-mode special-mode "EAM Status"
  "Observed session details.  g refreshes; j opens its note; q closes."
  (setq-local header-line-format " g refresh · j note · q close")
  (setq-local truncate-lines nil)
  (buffer-disable-undo)
  (add-hook 'kill-buffer-hook #'eam-usage--cancel nil t))
(put 'eam-status-mode 'completion-predicate #'ignore)
(put 'eam-status--refresh 'completion-predicate #'ignore)
;;;###autoload
(defun eam-status (directory)
  "Show verified local process metadata without starting or reattaching a CLI."
  (interactive (list (eam-session-context-directory)))
  (let ((info (eam-persistent--call "inspect" `((session . ,(expand-file-name directory))))))
    (pop-to-buffer (get-buffer-create "*EAM status*"))
    (eam-usage--cancel)
    (eam-status-mode)
    (setq-local eam-terminal-persistent-directory directory
                eam-status--info info
                eam-terminal--current (eam-terminal--session :name (alist-get 'provider (alist-get 'metadata info)))
                eam-usage--updated-function #'eam-status--render)
    (eam-status--render)
    (eam-usage--refresh (current-buffer) t)))

(defun eam-persistent--guard-worktree (directory)
  "Include detached persistent sessions in the worktree deletion check."
  (eam-persistent--call
   "guard-worktree" `((root . ,(expand-file-name "persistent" eam-directory))
                      (directory . ,directory))))
(add-hook 'eam-worktree-before-remove-hook #'eam-persistent--guard-worktree)
(provide 'eam-persistent)
;;; eam-persistent.el ends here
