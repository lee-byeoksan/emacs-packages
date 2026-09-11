;;; eam-remote.el --- Start and reuse the remote terminal server -*- lexical-binding: t; -*-
(require 'eam-native)
(require 'json)

(defcustom eam-remote-auth 'tailscale
  "Authentication method.  Tailscale allows only this machine owner's account.
Token mode is an explicit alternative for local use.  There is no fallback."
  :type '(choice (const tailscale) (const token)) :group 'eam)
(defvar eam-remote--user nil)

(defcustom eam-remote-origin nil
  "Public origin, or nil to discover this machine's Tailscale HTTPS name."
  :type '(choice (const nil) string) :group 'eam)
(defcustom eam-remote-port 8765 "Loopback port of the remote server."
  :type 'integer :group 'eam)
(defcustom eam-remote-browse-root (expand-file-name "~/")
  "Root for browsing and creating project folders."
  :type 'directory :group 'eam)
(defcustom eam-remote-tailscale-executable nil
  "Tailscale CLI, or nil to find it on PATH or in the macOS application."
  :type '(choice (const nil) file) :group 'eam)
(defvar eam-remote--restart-requested nil)
(defvar eam-remote--job nil)
(defvar eam-remote--timer nil)
(defvar eam-remote--url nil)
(defvar eam-remote--status "Not started")

(defun eam-remote--directory () (expand-file-name "remote/" eam-directory))
(defun eam-remote--token-file () (expand-file-name "token" (eam-remote--directory)))
(defun eam-remote--token ()
  (let ((file (eam-remote--token-file)))
    (when (file-exists-p file)
      (unless (and (not (file-symlink-p file))
                   (eq (file-attribute-user-id (file-attributes file)) (user-uid))
                   (zerop (logand (file-modes file) #o077)))
        (user-error "Remote token must be a private file owned by you: %s" file))
      (with-temp-buffer
        (insert-file-contents file nil 0 4097)
        (let ((token (string-trim (buffer-string))))
          (unless (and (<= (length token) 4096)
                       (string-match-p "\\`[A-Za-z0-9_-]+\\'" token))
            (user-error "Invalid remote token file: %s" file))
          token)))))
(defun eam-remote--report (text)
  (setq eam-remote--status text)
  (message "EAM remote: %s" text))

(defun eam-remote--probe (origin &optional shutdown)
  "Authenticate the loopback service, returning nil only if connection fails."
  (let ((buffer (generate-new-buffer " *eam-remote-probe*")) process)
    (unwind-protect
        (progn
          (setq process
                (condition-case nil
                    (make-network-process :name "eam-remote-probe" :buffer buffer
                                          :host "127.0.0.1" :service eam-remote-port
                                          :family 'ipv4 :coding 'binary :noquery t
                                          :sentinel #'ignore
                                          :filter (lambda (p text)
                                                    (with-current-buffer (process-buffer p)
                                                      (if (> (+ (buffer-size) (length text)) 16384)
                                                          (delete-process p)
                                                        (goto-char (point-max)) (insert text)))))
                  (file-error nil)))
          (when process
            (process-send-string
             process (format "%s HTTP/1.0\r\nHost: %s\r\nOrigin: %s\r\n%s\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"
                             (if shutdown "POST /api/shutdown" "GET /api/config")
                             (replace-regexp-in-string "\\`https?://" "" origin)
                             origin (if (eq eam-remote-auth 'tailscale)
                                        (concat "Tailscale-User-Login: " (or eam-remote--user ""))
                                      (concat "Cookie: eam_token=" (or (eam-remote--token) "")))))
            (let ((deadline (+ (float-time) 2)))
              (while (and (process-live-p process) (< (float-time) deadline))
                (accept-process-output process .05)))
            (with-current-buffer buffer
              (goto-char (point-min))
              (unless (and (looking-at "HTTP/1\\.[01] 200 ")
                           (search-forward "\r\n\r\n" nil t))
                (user-error "Port %d is occupied but origin/authentication does not match; server left running"
                            eam-remote-port))
              (let ((data (json-parse-string
                           (decode-coding-string (buffer-substring-no-properties (point) (point-max)) 'utf-8)
                           :object-type 'alist)))
                (unless (or shutdown (and (equal (alist-get 'runtime data) "rust")
                             (equal (alist-get 'origin data) origin)
                             (equal (alist-get 'auth data) (symbol-name eam-remote-auth))
                             (or (eq eam-remote-auth 'token)
                                 (equal (alist-get 'tailscale_user data) eam-remote--user))
                             (equal (alist-get 'sessions data)
                                    (directory-file-name (file-truename (expand-file-name "persistent" eam-directory))))
                             (equal (alist-get 'browse_root data)
                                    (directory-file-name (file-truename eam-remote-browse-root)))))
                  (user-error "Existing remote server has different settings; left running"))
                data))))
      (when (process-live-p process) (delete-process process))
      (kill-buffer buffer))))

(defun eam-remote--ready (origin)
  (setq eam-remote--url origin)
  (eam-remote--report (concat "Ready: " origin)))
(defun eam-remote--check-start (origin remaining)
  (setq eam-remote--timer nil)
  (condition-case err
      (if (eam-remote--probe origin)
          (eam-remote--ready origin)
        (if (> remaining 0)
            (setq eam-remote--timer
                  (run-at-time .25 nil #'eam-remote--check-start origin (1- remaining)))
          (eam-remote--report "Start failed; see remote/server.log")))
    (error (setq eam-remote--restart-requested nil) (eam-remote--report (error-message-string err)))))

(defun eam-remote--ensure-now (origin)
  (unless (string-match-p "\\`https?://[A-Za-z0-9.-]+\\(?::[0-9]+\\)?\\'" origin)
    (user-error "Invalid remote origin: %s" origin))
  (if (eam-remote--probe origin)
      (if eam-remote--restart-requested
          (progn
            (setq eam-remote--restart-requested nil)
            (eam-remote--probe origin t)
            (eam-remote--report "Returning control to Emacs and restarting…")
            (setq eam-remote--timer (run-at-time .25 nil #'eam-remote--await-stop origin 120)))
        (eam-remote--ready origin))
    (setq eam-remote--restart-requested nil)
    (let* ((binary (eam-native--executable))
           (directory (eam-remote--directory))
           (log (expand-file-name "server.log" directory))
           (default-directory (expand-file-name "~/"))
           (process-connection-type nil)
           (command (mapconcat #'shell-quote-argument
                               (append (list binary "serve" "--origin" origin
                                     "--port" (number-to-string eam-remote-port)
                                     "--sessions" (expand-file-name "persistent" eam-directory)
                                     "--browse-root" (expand-file-name eam-remote-browse-root))
                                       (if (eq eam-remote-auth 'tailscale)
                                           (list "--tailscale-user" eam-remote--user)
                                         (list "--token-file" (eam-remote--token-file)))) " ")))
      (make-directory directory t)
      (set-file-modes directory #o700)
      ;; No Emacs-owned pipe or PTY remains.  nohup preserves the server on Emacs exit.
      (setq eam-remote--job
            (make-process :name "eam-remote-launch" :buffer nil :noquery t
                          :command (list "/bin/sh" "-c"
                                         (concat "umask 077; nohup " command " >>"
                                                 (shell-quote-argument log) " 2>&1 </dev/null &"))))
      (setq eam-remote--timer (run-at-time .25 nil #'eam-remote--check-start origin 20))
      (eam-remote--report "Starting…"))))

(defun eam-remote--await-stop (origin remaining)
  (setq eam-remote--timer nil)
  ;; A draining server responds 503.  Wait for its listening socket to close.
  (let ((connection (condition-case nil
                        (make-network-process :name "eam-remote-drain" :host "127.0.0.1"
                                              :service eam-remote-port :family 'ipv4 :noquery t)
                      (file-error nil))))
    (if (not connection)
        (eam-remote--ensure origin)
      (delete-process connection)
      (if (> remaining 0)
          (setq eam-remote--timer (run-at-time .25 nil #'eam-remote--await-stop origin (1- remaining)))
        (eam-remote--report "Server is still shutting down; retry eam-remote-start later")))))

;;;###autoload
(defun eam-remote-restart ()
  "Gracefully restart the matching remote server, preserving CLI sessions."
  (interactive)
  (when (or (process-live-p eam-remote--job) (timerp eam-remote--timer))
    (user-error "Remote start/restart is already in progress"))
  (setq eam-remote--restart-requested t)
  (eam-remote-start))

(defun eam-remote--after-build (origin)
  (setq eam-remote--timer nil)
  (if (process-live-p eam-native--build-process)
      (setq eam-remote--timer (run-at-time 1 nil #'eam-remote--after-build origin))
    (if (and eam-native--build-process
             (eq (process-status eam-native--build-process) 'exit)
             (zerop (process-exit-status eam-native--build-process)))
        (eam-remote--ensure origin)
      (eam-remote--report "Build failed; run eam-native-build, then eam-remote-start"))))

(defun eam-remote--ensure (origin)
  (condition-case err
      (eam-remote--ensure-now origin)
    (error
     (setq eam-remote--restart-requested nil)
     (if (process-live-p eam-native--build-process)
         (progn
           (setq eam-remote--timer (run-at-time 1 nil #'eam-remote--after-build origin))
           (eam-remote--report "Waiting for native build…"))
       (eam-remote--report (error-message-string err))))))

(defun eam-remote--dns-origin (data)
  (let ((dns (alist-get 'DNSName (alist-get 'Self data))))
    (unless (and (equal (alist-get 'BackendState data) "Running")
                 (stringp dns) (not (string-empty-p dns)))
      (user-error "Connect Tailscale, then run M-x eam-remote-start"))
    (concat "https://" (string-remove-suffix "." dns))))

(defun eam-remote--owner (data)
  "Resolve this device's owner, never any arbitrary user in the tailnet."
  (let* ((self (alist-get 'Self data))
         (id (alist-get 'UserID self))
         (user (and (integerp id)
                    (alist-get (intern (number-to-string id)) (alist-get 'User data))))
         (login (alist-get 'LoginName user)))
    (unless (and (not (alist-get 'Tags self)) (stringp login)
                 (string-match-p "\\`[!-~]+\\'" login))
      (user-error "No personal Tailscale owner found; tagged devices cannot use owner authentication"))
    login))

;;;###autoload
(defun eam-remote-start ()
  "Start or reuse the remote server; discover Tailscale's address if unset.
Does not change Tailscale routing.  Configure Tailscale Serve once beforehand."
  (interactive)
  (unless (or (process-live-p eam-remote--job) (timerp eam-remote--timer))
    (condition-case err
        (if (and eam-remote-origin (eq eam-remote-auth 'token))
            (eam-remote--ensure eam-remote-origin)
          (let* ((cli (or eam-remote-tailscale-executable (executable-find "tailscale")
                          (let ((app "/Applications/Tailscale.app/Contents/MacOS/Tailscale"))
                            (and (file-executable-p app) app))
                          (user-error "Install/connect Tailscale or set eam-remote-origin")))
                 (buffer (generate-new-buffer " *eam-tailscale*"))
                 (process-connection-type nil))
            (setq eam-remote--job
                  (make-process
                   :name "eam-remote-discovery" :buffer buffer :noquery t
                   :command (list cli "status" "--json")
                   :sentinel
                   (lambda (process _event)
                     (unless (process-live-p process)
                       (when-let* ((timer (process-get process 'timeout))) (cancel-timer timer))
                       (unwind-protect
                           (condition-case error-data
                               (if (and (eq (process-status process) 'exit)
                                        (zerop (process-exit-status process)))
                                   (eam-remote--ensure
                                    (with-current-buffer (process-buffer process)
                                      (goto-char (point-min))
                                      (let* ((data (json-parse-buffer :object-type 'alist))
                                             (origin (eam-remote--dns-origin data)))
                                        (when (eq eam-remote-auth 'tailscale)
                                          (setq eam-remote--user (eam-remote--owner data)))
                                        (or eam-remote-origin origin))))
                                 (progn (setq eam-remote--restart-requested nil) (eam-remote--report "Tailscale unavailable; run eam-remote-start after connecting")))
                             (error (setq eam-remote--restart-requested nil) (eam-remote--report (error-message-string error-data))))
                         (kill-buffer (process-buffer process)))))))
            (process-put eam-remote--job 'timeout
                         (run-at-time 8 nil (lambda (p) (when (process-live-p p) (delete-process p)))
                                      eam-remote--job))))
      (error (setq eam-remote--restart-requested nil) (eam-remote--report (error-message-string err))))))

;;;###autoload
(defun eam-remote-info ()
  "Show connection address, authentication and server status."
  (interactive)
  (with-help-window "*EAM remote*"
    (princ (format "%s\n\nAddress: %s\nAuthentication: %s\nLog: %s\n\nM-x eam-remote-start starts or reuses the server.\nM-x eam-remote-restart restarts it while preserving CLI sessions.\n"
                   eam-remote--status (or eam-remote--url "Not discovered")
                   (if (eq eam-remote-auth 'tailscale)
                       (concat "Tailscale / " (or eam-remote--user "Not discovered"))
                     (concat "Token / " (or (eam-remote--token) "Not created")))
                   (expand-file-name "server.log" (eam-remote--directory))))))

;;;###autoload
(define-minor-mode eam-remote-mode
  "Start/reuse the remote server when enabled.  Disabling leaves it running.
Enable from your EAM configuration.  Merely requiring EAM does not start it."
  :global t :group 'eam
  (if eam-remote-mode
      (unless noninteractive
        (unless (timerp eam-remote--timer)
          (setq eam-remote--timer
                (run-at-time 0 nil (lambda () (setq eam-remote--timer nil) (eam-remote-start))))))
    (when (timerp eam-remote--timer) (cancel-timer eam-remote--timer))
    (setq eam-remote--timer nil)))

(provide 'eam-remote)
;;; eam-remote.el ends here
