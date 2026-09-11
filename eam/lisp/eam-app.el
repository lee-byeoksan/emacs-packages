;;; eam-app.el --- Keyboard-first AI workspace -*- lexical-binding: t; -*-
(declare-function eam-native-history-open "eam-native-history" (&optional provider directory prompt))
(require 'eam-terminal)
(require 'eam-caffeine)
(require 'eam-review)
(require 'eam-worktree)

;;;###autoload
(defun eam-new (provider directory &optional name)
  "Choose PROVIDER and DIRECTORY and start a persistent CLI with optional NAME."
  (interactive (let ((use-dialog-box nil))
                 (list (eam--read-provider)
                       (eam-terminal--read-project-directory)
                       (read-string "Session name (optional): "))))
  (eam-terminal-start-provider provider directory name))

;;;###autoload
(defun eam-detach-all ()
  "Detach all EAM terminal sessions connected in this Emacs.
Use the same lifecycle rules as `eam-detach': persistent CLIs keep running;
quick sessions stop.  Other Emacs instances and detached sessions are untouched."
  (interactive)
  (let ((sessions (copy-sequence eam-terminal--sessions))
        (count 0) failures)
    (dolist (session sessions)
      (let ((buffer (eam-terminal-output session)))
        (when (buffer-live-p buffer)
          (condition-case err
              (with-current-buffer buffer
                (eam-detach)
                (cl-incf count))
            (error (push (format "%s: %s" (buffer-name buffer)
                                 (error-message-string err)) failures))))))
    (if failures
        (user-error "Detached %d session(s); failures: %s"
                    count (string-join (nreverse failures) "; "))
      (message "Detached %d session(s); quick sessions stop" count))
    count))

(defcustom eam-quick-directory (expand-file-name "~/.eam/quick/")
  "Working directory for quick sessions, created on demand."
  :type 'directory :group 'eam)
;;;###autoload
(defun eam-quick (provider)
  "Start PROVIDER for a quick question in `eam-quick-directory'.
Closing or detaching its terminal stops the CLI.  Native CLI history remains."
  (interactive (list (eam--read-provider)))
  (let ((directory (expand-file-name eam-quick-directory)))
    (when (file-remote-p directory) (user-error "Quick sessions require a local directory"))
    (make-directory directory t)
    (eam-persistent--start provider directory nil "quick" t)))

;;;###autoload
(defun eam-prompt ()
  "Show the current session's observed request and tools without a picker."
  (interactive)
  (eam-status (eam-session-context-directory)))

;;;###autoload
(defun eam-help ()
  "View the installed keyboard command manual read-only; q returns."
  (interactive)
  (view-file (expand-file-name "docs/user-guide.md" eam--resource-directory)))

(defcustom eam-session-query-timeout 2.0
  "Total seconds to wait for local branch queries when selecting a session.
Unfinished queries are shown as unverified.  C-g cancels the selection."
  :type 'number :group 'eam)

(defun eam-app--branches (directories)
  "Query unique local DIRECTORIES with at most eight concurrent Git jobs.
Yield to Emacs events while waiting.  Bound output to 4096 characters per job,
Clean up on quit.  Return unverified metadata if the total deadline expires."
  (let ((pending (delete-dups (copy-sequence directories)))
        (results (make-hash-table :test #'equal))
        (deadline (+ (float-time) (max 0.01 eam-session-query-timeout)))
        active owned)
    (dolist (directory pending) (puthash directory "unverified" results))
    (unwind-protect
        (progn
          (while (and (or pending active) (< (float-time) deadline))
            (while (and pending (< (length active) 8) (< (float-time) deadline))
              (let ((directory (pop pending)))
                (if (or (null directory) (file-remote-p directory))
                    (puthash directory "—" results)
                  (condition-case nil
                      (let* ((default-directory (file-name-as-directory directory))
                             (process
                              (make-process
                               :name "eam-branch" :buffer nil :noquery t
                               :connection-type 'pipe :coding 'utf-8-unix
                               :command '("git" "symbolic-ref" "--quiet" "--short" "HEAD")
                               :sentinel #'ignore
                               :filter (lambda (process text)
                                         (let ((value (concat (process-get process 'branch-output) text)))
                                           (if (> (length value) 4096)
                                               (progn (process-put process 'overflow t)
                                                      (delete-process process))
                                             (process-put process 'branch-output value)))))))
                        (push process owned)
                        (push (cons directory process) active))
                    (file-error (puthash directory "—" results))))))
            (accept-process-output nil 0.01)
            (setq active
                  (cl-delete-if
                   (lambda (entry)
                     (let ((process (cdr entry)))
                       (unless (process-live-p process)
                         ;; Exit status can become visible before queued pipe output.
                         (while (accept-process-output process 0 nil t))
                         (puthash (car entry)
                                  (if (and (eq (process-status process) 'exit)
                                           (= (process-exit-status process) 0)
                                           (not (process-get process 'overflow)))
                                      (let ((value (string-trim
                                                    (or (process-get process 'branch-output) ""))))
                                        (if (string-empty-p value) "—" value))
                                    "—") results)
                         t))) active)))
          results)
      (dolist (process owned)
        (when (process-live-p process) (delete-process process))))))

(defun eam-app--branch (directory)
  "Read one branch using the bounded local query implementation."
  (gethash directory (eam-app--branches (list directory))))

(defun eam-app--process-state (session)
  "Describe SESSION's process, without inferring the agent's task state."
  (let ((process (eam-terminal-process session)))
    (concat
     (or (and (buffer-live-p (eam-terminal-output session))
              (buffer-local-value 'eam-persistent--display-state (eam-terminal-output session)))
         (if (not (processp process)) "not started"
       (let ((status (process-status process)))
         (if (memq status '(exit signal))
             (format "%s(%d)" status (process-exit-status process))
           (symbol-name status)))))
     (when (eam-terminal-error session) "; recording error"))))

(defun eam-app--session-candidates ()
  "Build completion choices with local metadata, once per project per call."
  (let* ((sessions (cl-remove-if-not
                    (lambda (session) (buffer-live-p (eam-terminal-output session)))
                    eam-terminal--sessions))
         (directories (delete-dups (mapcar #'eam-terminal-directory sessions)))
         (branches (eam-app--branches directories)))
    (cl-loop for session in sessions
             for buffer = (eam-terminal-output session)
             when (buffer-live-p buffer)
             collect
             (let* ((directory (eam-terminal-directory session))
                    (branch (gethash directory branches "unverified")))
               (cons (format "%s | %s | %s | branch:%s | process:%s"
                             (buffer-name buffer)
                             (eam-terminal-name session)
                             (if directory (abbreviate-file-name directory) "—")
                             branch (eam-app--process-state session))
                     buffer)))))

(defun eam-app--live-inventory (&optional observe)
  "Query persistent CLI state without blocking Emacs input or starting a CLI."
  (let ((buffer (generate-new-buffer " *eam-live-sessions*"))
        (deadline (+ (float-time) 10)) process extra)
    (dolist (session eam-terminal--sessions)
      (when (buffer-live-p (eam-terminal-output session))
        (when-let ((directory (buffer-local-value 'eam-terminal-persistent-directory
                                                 (eam-terminal-output session))))
          (cl-pushnew directory extra :test #'equal))))
    (unwind-protect
        (progn
          (setq process
                (make-process :name "eam-live-sessions" :buffer buffer :noquery t
                              :connection-type 'pipe :coding 'utf-8-unix :sentinel #'ignore
                              :command (list (eam-native--executable) "manager" "list-live")
                              :filter (lambda (proc text)
                                        (with-current-buffer (process-buffer proc)
                                          (if (> (+ (buffer-size) (length text)) 1048576)
                                              (delete-process proc)
                                            (goto-char (point-max)) (insert text))))))
          (process-send-string process
                               (json-encode `((root . ,(expand-file-name "persistent" eam-directory))
                                              (observe . ,(if observe t :json-false))
                                              (include_interrupted . ,(if observe t :json-false))
                                              (extra . ,(vconcat extra)))))
          (process-send-eof process)
          (while (and (process-live-p process) (< (float-time) deadline))
            (accept-process-output process .05))
          (when (process-live-p process) (user-error "Session query timed out; C-g can cancel"))
          (unless (zerop (process-exit-status process))
            (user-error "Could not verify live sessions; use eam-status"))
          (with-current-buffer buffer
            (goto-char (point-min))
            (json-parse-buffer :object-type 'alist :array-type 'list :null-object nil :false-object nil)))
      (when (and process (process-live-p process)) (delete-process process))
      (kill-buffer buffer))))

(defvar eam-worktree-command-map
  (let ((map (make-sparse-keymap)))
    (define-key map (kbd "c") #'eam-worktree-create)
    (define-key map (kbd "o") #'eam-worktree-open)
    (define-key map (kbd "s") #'eam-worktree-start)
    (define-key map (kbd "d") #'eam-worktree-remove)
    map))

(defvar eam-command-map
  (let ((map (make-sparse-keymap)))
    (dolist (binding '(("n" . eam-new)
                       ("s" . eam-quick) ("j" . eam-note)
                       ("a" . eam-attach) ("q" . eam-quit) ("i" . eam-status)
                       ("v" . eam-review-selection)
                       ("m" . eam-notifications)
                       ("l" . eam-sessions) ("r" . eam-resume)
                       ("e" . eam-edit-input)
                       ("b" . eam-draft)
                       ("y" . eam-draft-add-selection)
                       ("p" . eam-paste)
                       ("t" . eam-focus)
                       ("u" . eam-prompt)
                       ("h" . eam-history)
                       ("o" . eam-history-open)
                       ("d" . eam-detach)
                       ("f" . eam-caffeine-mode) ("?" . eam-help)))
      (define-key map (kbd (car binding)) (cdr binding)))
    (define-key map (kbd "w") eam-worktree-command-map)
    map)
  "Commands under the optional C-c a prefix.")

(defvar eam-keys-mode-map
  (let ((map (make-sparse-keymap)))
    (define-key map (kbd "C-c a") eam-command-map)
    map))

;;;###autoload
(define-minor-mode eam-keys-mode
  "Enable C-c a commands in this Emacs; disabling restores previous bindings.
Loading the package does not enable this mode.  Standalone launch enables it.
Ghostel char mode captures keys; use M-RET to return to semi-char first."
  :global t :init-value nil :lighter nil :group 'eam
  :keymap eam-keys-mode-map)

(require 'eam-persistent)
(require 'eam-session-list)
(require 'eam-note)
(eam-caffeine--watch)
(provide 'eam-app)
;;; eam-app.el ends here
