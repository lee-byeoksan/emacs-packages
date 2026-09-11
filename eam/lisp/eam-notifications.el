;;; eam-notifications.el --- Bounded terminal event inbox -*- lexical-binding: t; -*-
(require 'cl-lib)
(require 'eam)
(require 'eam-native)
(require 'tabulated-list)

(defcustom eam-cli-notifications t
  "Enable terminal notification options for new and resumed official CLIs.
Only launch arguments are changed; existing CLI settings files are not written.
Claude uses command hooks returning terminalSequence only, without AI context."
  :type 'boolean :group 'eam)

(defun eam-notifications-cli-args (provider)
  "Return notification-only startup arguments for PROVIDER."
  (when eam-cli-notifications
    (cond
     ((equal provider "Codex")
      '("-c" "tui.notifications=true" "-c" "tui.notification_method=\"osc9\""
        "-c" "tui.notification_condition=\"always\""))
     ((equal provider "Claude")
      (let ((command (mapconcat #'shell-quote-argument
                               (list (eam-native--executable) "notify-claude") " ")))
        (list "--settings"
              (json-encode
               `((hooks . ,(mapcar
                            (lambda (event)
                              (cons event (vector `((hooks . [((type . "command")
                                                               (command . ,command))])))))
                            '(Stop Notification PermissionRequest)))))))))))

(defcustom eam-notification-limit 100
  "Maximum notification entries retained in memory."
  :type 'natnum :group 'eam)
(defcustom eam-notification-text-limit 2048
  "Maximum characters retained per notification title or body."
  :type 'natnum :group 'eam)
(defcustom eam-notification-desktop nil
  "Forward newly received notifications to Ghostel's desktop backend.
Off by default.  Terminal text can include sensitive project information."
  :type 'boolean :group 'eam)
(cl-defstruct (eam-notice (:constructor eam-notice--make))
  id buffer title body time read count fingerprint session sequence)
(defvar eam-notifications--entries nil)
(defvar eam-notifications--sequence 0)

(defun eam-notifications--text (text)
  "Bound TEXT and remove control characters and text properties for display."
  (replace-regexp-in-string
   "[[:cntrl:]]" " "
   (substring-no-properties text 0 (min (length text) eam-notification-text-limit))))

(defun eam-notifications--receive (buffer title body &optional session sequence read)
  "Record an explicit terminal notification from BUFFER, without inferring state.
Merge identical unread events from the same buffer within two seconds.
Raw OSC bytes are archived only when diagnostic recording is enabled."
  (when (and (buffer-live-p buffer) (> eam-notification-limit 0))
    (let* ((fingerprint (secure-hash 'sha256 (prin1-to-string (list title body))))
           (title (eam-notifications--text title))
           (body (eam-notifications--text body))
           (now (float-time))
           (duplicate
            (cl-find-if (lambda (entry)
                          (and (eq buffer (eam-notice-buffer entry))
                               (not (eam-notice-read entry))
                               (< (- now (eam-notice-time entry)) 2)
                               (equal fingerprint (eam-notice-fingerprint entry))
                               (equal title (eam-notice-title entry))
                               (equal body (eam-notice-body entry))))
                        eam-notifications--entries)))
      (if duplicate (progn (cl-incf (eam-notice-count duplicate))
                           (setf (eam-notice-sequence duplicate) sequence))
        (push (eam-notice--make
               :id (cl-incf eam-notifications--sequence) :buffer buffer
               :title title :body body :time now :count 1 :fingerprint fingerprint
               :session session :sequence sequence :read read)
              eam-notifications--entries)
        (when (and eam-notification-desktop (not read))
          (condition-case err
              (with-current-buffer buffer (ghostel-default-notify title body))
            (error (message "Desktop notification failed: %s" (error-message-string err))))))
      (when (> (length eam-notifications--entries) eam-notification-limit)
        (setcdr (nthcdr (1- eam-notification-limit) eam-notifications--entries) nil)))))

(defun eam-notifications--acknowledge (session sequence &optional unread)
  "Persist SESSION's read position through SEQUENCE, or UNREAD from SEQUENCE."
  (require 'eam-persistent)
  (let* ((result (eam-persistent--call
                  "acknowledge" `((session . ,session) (seq . ,sequence)
                                  (unread . ,(if unread t :json-false)))))
         (read (alist-get 'read_seq result)))
    (dolist (notice eam-notifications--entries)
      (when (and (equal session (eam-notice-session notice))
                 (integerp (eam-notice-sequence notice)))
        (setf (eam-notice-read notice) (<= (eam-notice-sequence notice) read))))
    result))

(defun eam-notifications--visit (entry)
  (unless entry (user-error "No notification selected"))
  (unless (buffer-live-p (eam-notice-buffer entry))
    (user-error "This notification's session buffer has closed"))
  (pop-to-buffer (eam-notice-buffer entry))
  (if (eam-notice-session entry)
      (eam-notifications--acknowledge (eam-notice-session entry) (eam-notice-sequence entry))
    (setf (eam-notice-read entry) t)))

;;;###autoload
(defun eam-notification-next ()
  "Visit the oldest unread notification whose session buffer still exists."
  (interactive)
  (eam-notifications--visit
   (cl-find-if (lambda (entry) (and (not (eam-notice-read entry))
                                   (buffer-live-p (eam-notice-buffer entry))))
               (reverse eam-notifications--entries))))

(defun eam-notifications-visit ()
  "Visit the notification on the current row and mark it read."
  (interactive)
  (eam-notifications--visit (tabulated-list-get-id)))
(defun eam-notifications-toggle-read ()
  "Toggle read state for the notification on the current row."
  (interactive)
  (let ((entry (tabulated-list-get-id)))
    (unless entry (user-error "No notification selected"))
    (if (eam-notice-session entry)
        (eam-notifications--acknowledge (eam-notice-session entry)
                                        (eam-notice-sequence entry) (eam-notice-read entry))
      (setf (eam-notice-read entry) (not (eam-notice-read entry))))
    (eam-notifications-refresh)))
(defun eam-notifications-refresh ()
  "Refresh the bounded notification view."
  (interactive)
  (setq tabulated-list-entries
        (mapcar (lambda (entry)
                  (list entry
                        (vector (if (eam-notice-read entry) "read" "NEW")
                                (if (buffer-live-p (eam-notice-buffer entry))
                                    (buffer-name (eam-notice-buffer entry)) "closed")
                                (eam-notice-title entry)
                                (eam-notice-body entry)
                                (number-to-string (eam-notice-count entry)))))
                eam-notifications--entries))
  (tabulated-list-print t))
(define-derived-mode eam-notifications-mode tabulated-list-mode "AI Notices"
  "Terminal notifications; semantic task state is unverified.
RET visits, r toggles read state, g refreshes, q closes the view."
  (setq tabulated-list-format [("Read" 5 t) ("Session" 38 t) ("Title" 24 t)
                               ("Terminal event (state unverified)" 60 t) ("Count" 6 t)])
  (setq tabulated-list-padding 1)
  (add-hook 'tabulated-list-revert-hook #'eam-notifications-refresh nil t)
  (tabulated-list-init-header)
  (buffer-disable-undo))
(define-key eam-notifications-mode-map (kbd "RET") #'eam-notifications-visit)
(define-key eam-notifications-mode-map (kbd "r") #'eam-notifications-toggle-read)
(define-key eam-notifications-mode-map (kbd "g") #'eam-notifications-refresh)
;;;###autoload
(defun eam-notifications ()
  "Show explicit terminal events, without starting or prompting any CLI."
  (interactive)
  (pop-to-buffer (get-buffer-create "*AI notifications*"))
  (eam-notifications-mode)
  (eam-notifications-refresh))

(declare-function ghostel-default-notify "ghostel" (title body))
;;;###autoload
(dolist (command '(eam-notification-next
                    eam-notifications-mode
                    eam-notifications-refresh
                    eam-notifications-toggle-read
                    eam-notifications-visit))
  (put command 'completion-predicate #'ignore))

(provide 'eam-notifications)
;;; eam-notifications.el ends here
