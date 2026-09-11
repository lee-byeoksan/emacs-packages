;;; eam-session-list.el --- Grouped persistent session list -*- lexical-binding: t; -*-
(require 'eam-terminal)
(require 'eam-persistent)
(require 'eam-notifications)
(require 'hl-line)

(defface eam-session-directory-face
  '((t :inherit header-line :weight bold :extend t))
  "Directory section heading in the session list." :group 'eam)
(defface eam-session-name-face
  '((t :inherit default :weight bold))
  "User session name in the session list." :group 'eam)
(defface eam-session-unread-face
  '((t :inherit warning :weight bold))
  "Unread session notification indicator." :group 'eam)
(defface eam-session-attached-face
  '((t :inherit success))
  "Attached session indicator; independent of AI task state." :group 'eam)
(defface eam-session-remote-face
  '((t :inherit font-lock-constant-face :weight bold))
  "Session controlled by a remote web terminal." :group 'eam)
(defface eam-session-detail-face
  '((t :inherit shadow))
  "Secondary session information." :group 'eam)

(defcustom eam-session-sort-order 'activity
  "Initial session list ordering.  Activity means actual PTY input/output."
  :type '(choice (const activity) (const created) (const directory)
                 (const name) (const provider) (const state) (const unread)
                 (const context) (const work))
  :group 'eam)
(defcustom eam-session-group-by-directory t
  "Group sessions by working directory in the session list."
  :type 'boolean :group 'eam)
(defvar-local eam-session-list--entries nil)
(defvar-local eam-session-list--sort nil)
(defvar-local eam-session-list--group nil)
(defvar-local eam-session-list--detached nil)
(defvar-local eam-session-list--provider nil)
(defvar-local eam-session-list--unknown 0)
(defvar-local eam-session-list--checked-at nil)

(defun eam-session-list--at-point ()
  (get-text-property (line-beginning-position) 'eam-session))
(defun eam-session-list--move (direction)
  "Move to the next session in DIRECTION, skipping directory headings."
  (let ((origin (point)) found)
    (while (and (not found) (zerop (forward-line direction)))
      (setq found (eam-session-list--at-point)))
    (unless found (goto-char origin))))
(defun eam-session-list--next ()
  "Move to the next session row."
  (eam-session-list--move 1))
(defun eam-session-list--previous ()
  "Move to the previous session row."
  (eam-session-list--move -1))
(defun eam-session-list--path (entry)
  (alist-get 'session entry))
(defun eam-session-list--directory (entry)
  (directory-file-name (alist-get 'directory entry)))
(defun eam-session-list--time (entry key)
  (let ((value (alist-get key entry))) (if (numberp value) value 0)))
(defun eam-session-list--activity (entry)
  (max (eam-session-list--time entry 'last_input_at)
       (eam-session-list--time entry 'last_output_at)))
(defun eam-session-list--state (entry)
  (cond ((equal (alist-get 'state entry) "interrupted") "interrupted")
        ((zerop (or (alist-get 'attached_clients entry) 0)) "detached")
        ((eq t (alist-get 'remote entry)) "remote")
        (t "attached")))
(defun eam-session-list--label (entry)
  (let ((name (alist-get 'name entry)))
    (if (and (stringp name) (not (string-empty-p name))) name "(unnamed)")))
(defun eam-session-list--unread (entry)
  (eq t (alist-get 'unread (alist-get 'notifications entry))))
(defun eam-session-list--less (a b)
  "Sort deterministically, with recent activity and stable identity as ties."
  (let* ((key (pcase eam-session-list--sort
                ('created 'created_at) ('directory 'directory)
                ('name 'name) ('provider 'provider) (_ nil)))
         (av (pcase eam-session-list--sort
               ('state (eam-session-list--state a))
               ('work (eam-usage--activity-text (alist-get 'observation a)))
               ('context (or (eam-usage--context-percent (alist-get 'observation a)) -1))
               ('unread (if (eam-session-list--unread a) 1 0))
               ('activity (max (eam-session-list--activity a)
                               (eam-session-list--time a 'created_at)))
               (_ (or (alist-get key a) ""))))
         (bv (pcase eam-session-list--sort
               ('state (eam-session-list--state b))
               ('work (eam-usage--activity-text (alist-get 'observation b)))
               ('context (or (eam-usage--context-percent (alist-get 'observation b)) -1))
               ('unread (if (eam-session-list--unread b) 1 0))
               ('activity (max (eam-session-list--activity b)
                               (eam-session-list--time b 'created_at)))
               (_ (or (alist-get key b) "")))))
    (if (equal av bv)
        (if (= (eam-session-list--activity a) (eam-session-list--activity b))
            (string< (eam-session-list--path a) (eam-session-list--path b))
          (> (eam-session-list--activity a) (eam-session-list--activity b)))
      (if (and (numberp av) (numberp bv)) (> av bv)
        (string< (format "%s" av) (format "%s" bv))))))
(defun eam-session-list--text (value width)
  (truncate-string-to-width
   (replace-regexp-in-string "[[:cntrl:]]" " " (format "%s" value)) width nil nil "…"))
(defun eam-session-list--date (value)
  "Show relative age with an exact timestamp in the tooltip."
  (if (<= value 0) (propertize "—" 'face 'eam-session-detail-face)
    (let* ((age (max 0 (- (float-time) value)))
           (text (cond ((< age 60) "방금")
                       ((< age 3600) (format "%d분 전" (/ (floor age) 60)))
                       ((< age 86400) (format "%d시간 전" (/ (floor age) 3600)))
                       (t (format "%d일 전" (/ (floor age) 86400))))))
      (propertize text 'face 'eam-session-detail-face
                  'help-echo (format-time-string "%Y-%m-%d %H:%M:%S %Z"
                                                 (seconds-to-time value))))))
(defun eam-session-list--field (text width &optional face)
  "Pad TEXT to WIDTH display columns, retaining tooltips and faces."
  (concat (if face (propertize text 'face face) text)
          (make-string (max 0 (- width (string-width text))) ?\s)))
(defun eam-session-list--row (entry)
  (let* ((notices (alist-get 'notifications entry))
         (seq (alist-get 'seq notices))
         (start (point))
         (name (eam-session-list--label entry))
         (state (eam-session-list--state entry))
         (unread (eam-session-list--unread entry)))
    (insert "  "
            (eam-session-list--field
             (propertize (eam-session-list--text name 26) 'help-echo name)
             28 'eam-session-name-face)
            (eam-session-list--field
             (eam-session-list--text (alist-get 'provider entry) 7)
             9 'font-lock-type-face)
            (eam-session-list--field state 14
                                     (cond ((equal state "interrupted") 'warning)
                                           ((equal state "attached") 'eam-session-attached-face)
                                           ((equal state "remote") 'eam-session-remote-face)
                                           (t 'eam-session-detail-face)))
            (eam-session-list--field
             (eam-usage--activity-text (alist-get 'observation entry)) 12 'font-lock-keyword-face)
            (let ((percent (eam-usage--context-percent (alist-get 'observation entry))))
              (eam-session-list--field
               (propertize (if (numberp percent) (format "%.0f%%" percent) "—")
                           'help-echo (format "Usage source time: %s"
                                              (or (alist-get 'context_timestamp (alist-get 'observation entry))
                                                  (alist-get 'timestamp (alist-get 'observation entry)) "unknown"))) 7
               (cond ((not (numberp percent)) 'shadow) ((>= percent 90) 'error)
                     ((>= percent 75) 'warning) (t 'success))))
            (eam-session-list--field
             (cond (unread "● NEW")
                   ((and (numberp seq) (> seq 0)) "✓ read")
                   ((null notices) "unknown") (t "—"))
             9 (if unread 'eam-session-unread-face 'eam-session-detail-face))
            (eam-session-list--field (eam-session-list--date (eam-session-list--activity entry)) 13)
            (eam-session-list--field (eam-session-list--date (eam-session-list--time entry 'created_at)) 13)
            (propertize
             (concat (unless eam-session-list--group
                       (concat (eam-session-list--text
                                (abbreviate-file-name (eam-session-list--directory entry)) 120) " | "))
                     (eam-session-list--text (file-name-nondirectory (eam-session-list--path entry)) 80))
             'face 'eam-session-detail-face 'help-echo (eam-session-list--path entry))
            "\n")
    (add-text-properties start (point) `(eam-session ,entry mouse-face highlight))))
(defun eam-session-list--render (&optional selected)
  (let* ((selected (or selected (eam-session-list--path (eam-session-list--at-point))))
         (inhibit-read-only t)
         (entries (sort (cl-remove-if
                         (lambda (e)
                           (or (and eam-session-list--detached
                                    (not (equal (eam-session-list--state e) "detached")))
                               (and eam-session-list--provider
                                    (not (equal (alist-get 'provider e)
                                                eam-session-list--provider)))))
                         (copy-sequence eam-session-list--entries))
                        #'eam-session-list--less)) groups)
    (erase-buffer)
    (insert (propertize (format "EAM sessions · %d listed · %d unverified · sort: %s · %s · provider: %s\n"
                    (length entries) eam-session-list--unknown eam-session-list--sort
                    (if eam-session-list--detached "detached only" "active + interrupted")
                    (or eam-session-list--provider "All"))
                        'face 'bold))
    (insert (propertize
             (format "  Snapshot: %s · g refresh · ~ inferred work state; — unknown context\n"
                     (if eam-session-list--checked-at
                         (format-time-string "%F %T" eam-session-list--checked-at) "not checked"))
             'face 'eam-session-detail-face))
    (insert "\n  " (propertize
                       (concat (eam-session-list--field "SESSION" 28)
                               (eam-session-list--field "PROVIDER" 9)
                               (eam-session-list--field "STATE" 14)
                               (eam-session-list--field "WORK" 12)
                               (eam-session-list--field "CTX" 7)
                               (eam-session-list--field "NOTICE" 9)
                               (eam-session-list--field "LAST I/O" 13)
                               (eam-session-list--field "CREATED" 13) "ID / DIRECTORY")
                       'face 'eam-session-detail-face) "\n")
    (if (null entries) (insert "\nNo sessions in this view.\n")
      (if (not eam-session-list--group) (mapc #'eam-session-list--row entries)
        (dolist (entry entries)
          (let* ((dir (eam-session-list--directory entry)) (group (assoc dir groups)))
            (if group (setcdr group (cons entry (cdr group)))
              (push (list dir entry) groups))))
        (dolist (group (nreverse groups))
          (insert "\n" (propertize
                         (format "  %s  ·  %d sessions\n"
                                 (eam-session-list--text (abbreviate-file-name (car group)) 160)
                                 (length (cdr group)))
                         'face 'eam-session-directory-face 'help-echo (car group)))
          (mapc #'eam-session-list--row (nreverse (cdr group))))))
    (goto-char (point-min))
    (let (first found)
      (while (and (not found) (< (point) (point-max)))
        (when-let ((entry (eam-session-list--at-point)))
          (unless first (setq first (point)))
          (when (equal selected (eam-session-list--path entry)) (setq found (point))))
        (unless found (forward-line 1)))
      (goto-char (or found first (point-min))))
    (set-buffer-modified-p nil)))
(defun eam-session-list--refresh ()
  (let ((inventory (eam-app--live-inventory t)))
    (setq eam-session-list--entries (alist-get 'sessions inventory)
          eam-session-list--checked-at (float-time)
          eam-session-list--unknown (length (alist-get 'unverified inventory)))
    (eam-session-list--render)))
(defun eam-session-list--ack (entry)
  "Acknowledge only the snapshot sequence, leaving later events unread."
  (let ((seq (alist-get 'seq (alist-get 'notifications entry))))
    (when (and (integerp seq) (> seq 0))
      (eam-notifications--acknowledge (eam-session-list--path entry) seq))))
(defun eam-session-list--buffer (entry)
  "Find the live buffer owned by this Emacs for ENTRY."
  (when-let ((session
              (cl-find-if
               (lambda (s)
                 (and (buffer-live-p (eam-terminal-output s))
                      (process-live-p (eam-terminal-process s))
                      (equal (directory-file-name (eam-session-list--path entry))
                             (when-let ((p (buffer-local-value 'eam-terminal-persistent-directory
                                                               (eam-terminal-output s))))
                               (directory-file-name p))))) eam-terminal--sessions)))
    (eam-terminal-output session)))
(defun eam-session-list--recover (entry)
  "Resume interrupted ENTRY using its saved provider profile and conversation."
  (let* ((provider (alist-get 'provider entry))
         (id (alist-get 'resume_id entry))
         (directory (alist-get 'directory entry))
         (root (alist-get 'auth_root entry))
         (process-environment (copy-sequence process-environment)))
    (unless (member provider '("Claude" "Codex"))
      (user-error "Unsupported recovery provider: %s" provider))
    (unless (file-directory-p directory)
      (user-error "Original project directory is missing: %s" directory))
    (when root
      (if (equal provider "Codex") (setenv "CODEX_HOME" root)
        (setenv "CLAUDE_CONFIG_DIR"
                (when (eq t (alist-get 'auth_root_override entry)) root))))
    (eam-persistent--start
     provider directory
     (append (if (equal provider "Claude") '("--resume") '("resume"))
             (when id (list id)))
     (alist-get 'name entry) (eq t (alist-get 'temporary entry))
     (eam-session-list--path entry))))
(defun eam-session-list--visit (entry)
  "Visit running ENTRY or resume it if interrupted, then acknowledge notices."
  (if (equal (eam-session-list--state entry) "interrupted")
      (eam-session-list--recover entry)
    (if-let ((buffer (eam-session-list--buffer entry))) (pop-to-buffer buffer)
      (if (equal (eam-session-list--state entry) "detached")
          (eam-attach (eam-session-list--path entry))
        (user-error "Session is attached in another Emacs; detach it there first"))))
  (eam-session-list--ack entry))
(defun eam-session-list--open ()
  (let ((entry (or (eam-session-list--at-point) (user-error "Select a session row")))
        (buffer (current-buffer)))
    (eam-session-list--visit entry)
    (with-current-buffer buffer (eam-session-list--refresh))))
(defun eam-session-list--cycle (direction)
  "Cycle sessions attached in this Emacs in creation order by DIRECTION."
  (unless (and eam-terminal--current
               (buffer-live-p (eam-terminal-output eam-terminal--current)))
    (user-error "Use an EAM session buffer"))
  (let* ((current (buffer-local-value 'eam-terminal-persistent-directory
                                     (eam-terminal-output eam-terminal--current)))
         (entries (sort
                   (seq-filter (lambda (e) (and (equal (eam-session-list--state e) "attached")
                                              (eam-session-list--buffer e)))
                               (alist-get 'sessions (eam-app--live-inventory)))
                   (lambda (a b)
                     (let ((at (eam-session-list--time a 'created_at))
                           (bt (eam-session-list--time b 'created_at)))
                       (if (= at bt) (string< (eam-session-list--path a) (eam-session-list--path b))
                         (< at bt))))))
         (index (cl-position current entries :key #'eam-session-list--path :test #'equal)))
    (if (or (null entries) (and index (= (length entries) 1)))
        (message "No other attached EAM session in this Emacs")
      (let* ((entry (nth (if index (mod (+ index direction) (length entries))
                           (if (> direction 0) 0 (1- (length entries)))) entries))
             (buffer (eam-session-list--buffer entry)))
        ;; Recheck after inventory: never attach if the target disconnected.
        (unless (buffer-live-p buffer) (user-error "Session is no longer attached"))
        (let ((switch-to-buffer-obey-display-actions nil)
              (switch-to-buffer-preserve-window-point t))
          (switch-to-buffer buffer))
        (eam-session-list--ack entry)))))
;;;###autoload
(defun eam-next-session ()
  "Visit the next session attached in this Emacs, in creation order."
  (interactive)
  (eam-session-list--cycle 1))
;;;###autoload
(defun eam-previous-session ()
  "Visit the previous session attached in this Emacs, in creation order."
  (interactive)
  (eam-session-list--cycle -1))
(define-key eam-terminal-controls-mode-map (kbd "C-M-n") #'eam-next-session)
(define-key eam-terminal-controls-mode-map (kbd "C-M-p") #'eam-previous-session)
(defun eam-session-list--sort-select ()
  (setq eam-session-list--sort
        (intern (completing-read "Sort sessions: "
                                 '("activity" "created" "directory" "name" "provider" "state" "unread" "context" "work")
                                 nil t nil nil (symbol-name eam-session-list--sort))))
  (eam-session-list--render))
(defun eam-session-list--provider-select ()
  "Filter this list by provider, or select All to clear the filter."
  (let ((choice (completing-read "Show provider: " '("All" "Codex" "Claude")
                                 nil t nil nil (or eam-session-list--provider "All"))))
    (setq eam-session-list--provider (unless (equal choice "All") choice))
    (eam-session-list--render)))
;;;###autoload
(defun eam-rename (name)
  "Persist NAME for the selected session.  Empty NAME restores automatic naming."
  (interactive (list (read-string "Session name (empty = automatic): "
                                  (alist-get 'name (eam-session-list--at-point)))))
  (let* ((entry (eam-session-list--at-point))
         (path (or (eam-session-list--path entry)
                   eam-terminal-persistent-directory
                   (and eam-terminal--current
                        (buffer-live-p (eam-terminal-output eam-terminal--current))
                        (buffer-local-value 'eam-terminal-persistent-directory
                                            (eam-terminal-output eam-terminal--current)))
                   (user-error "Use a session list, CLI or draft buffer")))
         (metadata (eam-persistent--call "rename" `((session . ,path) (name . ,name)))))
    (dolist (s eam-terminal--sessions)
      (when (and (buffer-live-p (eam-terminal-output s))
                 (equal (directory-file-name path)
                        (when-let ((p (buffer-local-value 'eam-terminal-persistent-directory
                                                          (eam-terminal-output s))))
                          (directory-file-name p))))
        (eam-persistent--set-label s (alist-get 'name metadata))))
    (when (derived-mode-p 'eam-session-list-mode) (eam-session-list--refresh))
    (message "Session name saved")))
(defvar eam-session-list-mode-map
  (let ((map (make-sparse-keymap)))
    (define-key map (kbd "n") (lambda () (interactive) (eam-session-list--next)))
    (define-key map (kbd "p") (lambda () (interactive) (eam-session-list--previous)))
    (define-key map (kbd "RET") (lambda () (interactive) (eam-session-list--open)))
    (define-key map (kbd "g") (lambda () (interactive) (eam-session-list--refresh)))
    (define-key map (kbd "s") (lambda () (interactive) (eam-session-list--sort-select)))
    (define-key map (kbd "v") (lambda () (interactive) (eam-session-list--provider-select)))
    (define-key map (kbd "r") #'eam-rename)
    (define-key map (kbd "i") #'eam-status)
    (define-key map (kbd "j") #'eam-note)
    (define-key map (kbd "m") (lambda () (interactive)
                                (eam-session-list--ack (or (eam-session-list--at-point)
                                                          (user-error "Select a session row")))
                                (eam-session-list--refresh)))
    (define-key map (kbd "t") (lambda () (interactive)
                                (setq eam-session-list--group (not eam-session-list--group))
                                (eam-session-list--render)))
    (define-key map (kbd "f") (lambda () (interactive)
                                (setq eam-session-list--detached (not eam-session-list--detached))
                                (eam-session-list--render)))
    map))
(define-derived-mode eam-session-list-mode special-mode "EAM Sessions"
  "Active and interrupted sessions grouped by directory.  See the buffer's key guide."
  (setq truncate-lines t)
  (setq-local header-line-format
              (list " " (mapconcat
                         (lambda (binding)
                           (concat (propertize (car binding) 'face 'help-key-binding)
                                   " " (cdr binding)))
                         '(("n/p" . "next/previous") ("RET" . "open/resume") ("g" . "refresh") ("s" . "sort")
                           ("r" . "rename") ("i" . "details") ("j" . "note") ("m" . "read") ("t" . "group")
                           ("f" . "detached") ("v" . "provider") ("q" . "close")) "  ·  ")))
  (hl-line-mode 1)
  (buffer-disable-undo))
(put 'eam-session-list-mode 'completion-predicate #'ignore)
;;;###autoload
(defun eam-sessions (&optional detached-only)
  "Show active and interrupted sessions; DETACHED-ONLY filters to live detached CLIs."
  (interactive "P")
  (require 'eam-app)
  (let ((directory default-directory))
    (pop-to-buffer (get-buffer-create "*EAM sessions*"))
    (unless (derived-mode-p 'eam-session-list-mode)
      (eam-session-list-mode)
      (setq eam-session-list--sort eam-session-sort-order
            eam-session-list--group eam-session-group-by-directory))
    (setq default-directory directory eam-session-list--detached detached-only)
    (eam-session-list--refresh)))
(declare-function eam-app--live-inventory "eam-app" ())
(provide 'eam-session-list)
;;; eam-session-list.el ends here
