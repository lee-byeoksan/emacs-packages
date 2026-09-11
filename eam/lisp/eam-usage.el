;;; eam-usage.el --- Passive native usage in terminal headers -*- lexical-binding: t; -*-
(require 'eam-native-history)
(require 'eam-terminal)
(require 'seq)
(defcustom eam-usage-display t
  "Show provider quota and session tokens.  Never sends a model request."
  :type 'boolean :group 'eam)
(defcustom eam-account-display t
  "Show local account identity in the header, not a verified live login."
  :type 'boolean :group 'eam)
(defcustom eam-usage-refresh-interval 10
  "Seconds between usage refreshes for visible session buffers.
At most two local workers run together.  No worker runs during redisplay."
  :type 'number :group 'eam)
(defcustom eam-account-plan-labels nil
  "Display labels keyed by provider and the exact reported plan.
Map the provider and raw plan string to a descriptive display name.
These are user-supplied labels, not verified subscription multipliers."
  :type '(alist :key-type string :value-type (alist :key-type string :value-type string))
  :group 'eam)
(defvar-local eam-usage--saved-tab-line nil)
(defvar-local eam-usage--owns-tab-line nil)
(defvar-local eam-usage--timer nil)
(defvar-local eam-usage--process nil)
(defvar-local eam-usage--retry nil)
(defvar-local eam-usage--snapshot nil)
(defvar-local eam-usage--label "")
(defvar-local eam-usage--checked-at nil)
(defvar-local eam-usage--error nil)
(defvar-local eam-usage--updated-function nil)
(defvar eam-usage--workers 0)
(defun eam-usage--accept (value)
  "Keep last good values on worker failure and mark their freshness honestly."
  (setq eam-usage--checked-at (float-time)
        eam-usage--error (not (and value (not (alist-get 'error value)))))
  (unless eam-usage--error
    (setq eam-usage--snapshot value))
  (when eam-usage--updated-function (funcall eam-usage--updated-function))
  (force-mode-line-update t))
(defun eam-usage--context-percent (v)
  (or (alist-get 'context_percent v)
      (let ((used (alist-get 'context_used v)) (limit (alist-get 'context_limit v)))
        (when (and (numberp used) (numberp limit) (> limit 0))
          (* 100.0 (/ used (float limit)))))))
(defun eam-usage--activity-text (v)
  (let ((a (alist-get 'activity v)))
    (concat (or (alist-get 'state a) "unknown")
            (when (alist-get 'estimated a) "~"))))
(defun eam-usage--freshness ()
  (let* ((quota (alist-get 'provider_usage eam-usage--snapshot))
         (stamp (alist-get 'timestamp quota)))
    (concat (when eam-usage--error " · refresh failed; last values")
            (when (alist-get 'refresh_error quota) " · quota refresh failed")
            (when (numberp stamp)
              (format " · quota %ds ago" (max 0 (floor (- (float-time) stamp)))))
            (when (numberp eam-usage--checked-at)
              (format " · checked %ds ago" (max 0 (floor (- (float-time) eam-usage--checked-at))))))))
(defun eam-usage--number (value)
  (if (and (numberp value) (>= value 0))
      (cond ((>= value 1000000) (format "%.1fM" (/ value 1000000.0)))
            ((>= value 1000) (format "%.1fk" (/ value 1000.0)))
            (t (number-to-string value))) "—"))
(defun eam-usage--text ()
  "Format cached values only; never read disk or start a process here."
  (let ((v eam-usage--snapshot))
    (cond ((not eam-usage-display) "")
          ((equal (alist-get 'state v) "unbound") " 대기")
          ((not (equal (alist-get 'state v) "available")) " 대기")
          (t
           (concat " "
                   (if (equal (alist-get 'scope v) "saved")
                       (concat "saved tokens " (eam-usage--number (alist-get 'saved v)))
                     (concat (if (equal (alist-get 'scope v) "last") "last " "total ")
                             "in " (eam-usage--number (alist-get 'input v))
                             " / out " (eam-usage--number (alist-get 'output v))
                             (when (numberp (alist-get 'cached v))
                               (concat " / cache " (eam-usage--number (alist-get 'cached v))))
                             (when (numberp (alist-get 'cache_write v))
                               (concat " / write " (eam-usage--number (alist-get 'cache_write v))))))
                   (when (and (numberp (alist-get 'context_used v))
                              (numberp (alist-get 'context_limit v))
                              (> (alist-get 'context_limit v) 0))
                     (format " · ctx %s/%s"
                             (eam-usage--number (alist-get 'context_used v))
                             (eam-usage--number (alist-get 'context_limit v))))
                   (when (numberp (alist-get 'context_percent v))
                     (format " · ctx %.0f%%" (alist-get 'context_percent v))))))))
(defun eam-usage--account-text ()
  "Format only allowlisted local account identity fields."
  (when eam-account-display
    (let* ((account (alist-get 'account eam-usage--snapshot))
           (email (or (alist-get 'name account) (alist-get 'email account)))
           (reported-plan (alist-get 'plan account))
           (provider (and eam-terminal--current (eam-terminal-name eam-terminal--current)))
           (plan (or (cdr (assoc reported-plan (cdr (assoc provider eam-account-plan-labels))))
                     reported-plan))
           (tier (alist-get 'tier account)))
      (concat " | "
              (if (equal (alist-get 'state account) "local")
                  (concat (if (stringp email)
                              (truncate-string-to-width email 32 nil nil "…")
                            (or (alist-get 'method account) "—"))
                          (cond ((stringp plan) (concat " (" plan ")"))
                                ((stringp tier) (concat " (" tier ")"))))
                "—")))))
(defun eam-usage--provider-text ()
  "Format provider quota separately from conversation token counts."
  (when eam-usage-display
    (let* ((v (alist-get 'provider_usage eam-usage--snapshot))
           (windows (alist-get 'windows v))
           (age (and (numberp (alist-get 'timestamp v))
                     (- (float-time) (alist-get 'timestamp v)))))
      (concat " | "
              (if (seq-empty-p windows) "—"
                (mapconcat
                 (lambda (w)
                   (let ((mins (alist-get 'minutes w)) (used (alist-get 'used w))
                         (reset (alist-get 'resets w)))
                     (concat (cond ((= mins 10080) "7d") ((= mins 300) "5h")
                                   (t (format "%gm" mins)))
                             (format " %.0f%% used" used)
                             (when (and (numberp reset) (> reset 0))
                               (concat " ↻" (format-time-string "%m/%d %H:%M" reset))))))
                 windows " · "))
              (when (numberp (alist-get 'lifetime_tokens v))
                (concat " · total " (eam-usage--number (alist-get 'lifetime_tokens v))))
              (let* ((today (format-time-string "%Y-%m-%d" nil t))
                     (start (format-time-string "%Y-%m-%d" (time-subtract nil (days-to-time 6)) t))
                     (rows (seq-filter (lambda (r) (let ((d (alist-get 'date r)))
                                                    (and (stringp d) (not (string< d start))
                                                         (not (string< today d)))))
                                       (alist-get 'daily v))))
                (when rows
                  (format " · 7d recorded %s"
                          (eam-usage--number (apply #'+ (mapcar (lambda (r) (or (alist-get 'tokens r) 0)) rows))))))
              (when (and age (> age 120)) " [cached]")
              (eam-usage--freshness)))))
(defun eam-usage--tooltip ()
  (concat "Account source may differ from the running CLI login.\n"
          "Plan label overrides are user supplied; raw plan is shown below.\n"
          "Quota and session tokens are separate; missing values are not zero.\n"
          (prin1-to-string (if eam-account-display eam-usage--snapshot
                             (assq-delete-all 'account (copy-tree eam-usage--snapshot))))))
(defun eam-usage--account-header ()
  "First row: cached account and provider usage."
  (propertize (concat " " (truncate-string-to-width eam-usage--label 24 nil nil "…")
                      (eam-usage--account-text) (eam-usage--provider-text))
              'face 'header-line 'help-echo (eam-usage--tooltip)))
(defun eam-usage--header ()
  "Second row: cached session usage only."
  (propertize (concat (eam-usage--text)
                      (when eam-usage-display
                        (concat " · " (eam-usage--activity-text eam-usage--snapshot))))
              'help-echo (eam-usage--tooltip)))
(defun eam-usage--literal (text)
  "Escape TEXT for mode-line processing, preserving literal percent signs."
  (replace-regexp-in-string "%" "%%" text t t))
(defun eam-usage--root ()
  (expand-file-name (if (equal (eam-terminal-name eam-terminal--current) "Claude")
                       eam-claude-history-directory eam-codex-history-directory)))
(defun eam-usage--request (&optional entry)
  `((action . "poll")
    (session . ,eam-terminal-persistent-directory) (root . ,(eam-usage--root))
    (account . ,(if eam-account-display t :json-false))
    (usage . ,(if (or entry eam-usage-display) t :json-false))))
(defun eam-usage--cancel ()
  (when eam-usage--owns-tab-line
    (if (car eam-usage--saved-tab-line)
        (setq-local tab-line-format (cdr eam-usage--saved-tab-line))
      (kill-local-variable 'tab-line-format))
    (setq eam-usage--owns-tab-line nil))
  (when eam-usage--timer (cancel-timer eam-usage--timer) (setq eam-usage--timer nil))
  (when eam-usage--retry (cancel-timer eam-usage--retry) (setq eam-usage--retry nil))
  (when (process-live-p eam-usage--process) (delete-process eam-usage--process)))
(defun eam-usage--refresh (buffer &optional entry)
  "Refresh BUFFER asynchronously, bounded to 16 KiB and eight seconds."
  (when (buffer-live-p buffer)
    (with-current-buffer buffer
      (when (and (or entry eam-usage-display eam-account-display) eam-terminal-persistent-directory
                 (or entry (get-buffer-window buffer t)))
        (if (or (process-live-p eam-usage--process) (>= eam-usage--workers 2))
            (progn
              (when (and entry eam-usage--retry)
                (cancel-timer eam-usage--retry) (setq eam-usage--retry nil))
              (unless eam-usage--retry
                (setq eam-usage--retry
                      (run-at-time 1 nil
                                   (lambda ()
                                     (when (buffer-live-p buffer)
                                       (with-current-buffer buffer
                                         (setq eam-usage--retry nil)
                                         (eam-usage--refresh buffer entry))))))))
        (let ((request (eam-usage--request entry)) (output "") timer finished)
          (condition-case nil
              (progn
                (cl-incf eam-usage--workers)
                (setq eam-usage--process
                      (make-process
                       :name "eam-usage" :buffer nil :noquery t :connection-type 'pipe
                       :coding 'utf-8-unix :command (list (eam-native--executable) "usage")
                       :filter (lambda (proc text)
                                 (if (> (+ (length output) (length text)) 16384)
                                     (delete-process proc) (setq output (concat output text))))
                       :sentinel
                       (lambda (proc _event)
                         (when (and (memq (process-status proc) '(exit signal)) (not finished))
                           (setq finished t)
                           (cl-decf eam-usage--workers)
                           (when timer (cancel-timer timer))
                           (when (buffer-live-p buffer)
                             (with-current-buffer buffer
                               (eam-usage--accept
                                (condition-case nil
                                         (and (zerop (process-exit-status proc))
                                              (json-parse-string output :object-type 'alist
                                                                 :null-object nil :false-object nil))
                                       (error nil)))))))))
                (setq timer (run-at-time 8 nil
                                         (lambda (proc) (when (process-live-p proc) (delete-process proc)))
                                         eam-usage--process))
                (process-send-string eam-usage--process (json-encode request))
                (process-send-eof eam-usage--process))
            (error
             (eam-usage--accept nil)
             (unless finished
               (if (process-live-p eam-usage--process) (delete-process eam-usage--process)
                 (setq finished t) (cl-decf eam-usage--workers)))
             (when timer (cancel-timer timer))))))))))
(defun eam-usage--install (name)
  "Install a cheap cached header and passive polling for this terminal buffer."
  (setq eam-usage--label (if (string-empty-p (or name "")) "EAM" name))
  (unless eam-usage--owns-tab-line
    (setq eam-usage--saved-tab-line (cons (local-variable-p 'tab-line-format) tab-line-format)
          eam-usage--owns-tab-line t))
  (setq-local tab-line-format '(:eval (eam-usage--literal (eam-usage--account-header))))
  (setq-local header-line-format '(:eval (eam-usage--literal (eam-usage--header))))
  (unless eam-usage--timer
    (setq eam-usage--timer (run-at-time 1 (max 2 eam-usage-refresh-interval)
                                      #'eam-usage--refresh (current-buffer)))
    (add-hook 'kill-buffer-hook #'eam-usage--cancel nil t)))
;;;###autoload
(defun eam-usage ()
  "Refresh automatic provider and session usage without choosing a conversation."
  (interactive)
  (unless (and eam-terminal--current
               (buffer-live-p (eam-terminal-output eam-terminal--current)))
    (user-error "Use a CLI or draft buffer"))
  (eam-usage--refresh (eam-terminal-output eam-terminal--current) t)
  (message "Refreshing provider and session usage…"))
(provide 'eam-usage)
;;; eam-usage.el ends here
