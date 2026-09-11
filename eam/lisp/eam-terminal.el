;;; eam-terminal.el --- Native CLI with an Emacs draft -*- lexical-binding: t; -*-
(declare-function eam-native-history-open "eam-native-history" (&optional provider directory prompt))
(require 'eam-connect)
(require 'eam-native)
(require 'json)
(require 'server)
(defcustom eam-record-terminal nil
  "Record raw terminal output and EAM input events for newly started sessions.
Off by default.  Enable only when diagnosing terminal problems."
  :type 'boolean :group 'eam)
(defcustom eam-terminal-redraw-delay 0.016
  "Delay in seconds for batching terminal redraws.
Lower values favor responsiveness; higher values reduce redraw work.
Ghostel may redraw earlier for interactive input or after idle periods.
Changes apply to newly opened terminal buffers, including reattachments."
  :type 'number :group 'eam)
(defconst eam-terminal--dependency
  (expand-file-name "var/deps/ghostel/lisp" eam--resource-directory))
(unless (locate-library "ghostel")
  (when (file-readable-p (expand-file-name "ghostel.el" eam-terminal--dependency))
    (add-to-list 'load-path eam-terminal--dependency)))
(require 'ghostel)
(require 'eam-terminal-display)
(require 'eam-notifications)
(cl-defstruct (eam-terminal (:constructor eam-terminal--session))
  input output process file input-file error name directory resume-id)
(defvar-local eam-terminal--current nil)
(defvar-local eam-terminal-persistent-directory nil
  "Persistent session metadata directory, or nil for an Emacs-owned CLI.")
(defvar-local eam-terminal-temporary nil
  "Non-nil when disconnecting this display also stops its CLI.")
(defvar eam-terminal--sessions nil)
(defun eam-terminal--buffer-name (name directory &optional draft)
  "Identify a CLI by its provider and full abbreviated project path."
  (format "*%s %s: %s*" name (if draft "draft" "terminal")
          (abbreviate-file-name (directory-file-name directory))))
(defun eam-terminal--header ()
  "Describe this session without polling the CLI or starting a timer."
  (let* ((s eam-terminal--current)
         (p (and s (eam-terminal-process s))))
    (when s
      (format "%s | %s | %s%s"
              (eam-terminal-name s)
              (cond ((eam-terminal-error s) "recording error")
                    ((null p) "starting")
                    ((eq (process-status p) 'run) "running")
                    ((eq (process-status p) 'exit)
                     (format "exited (%d)" (process-exit-status p)))
                    ((eq (process-status p) 'signal)
                     (format "signal (%d)" (process-exit-status p)))
                    (t (symbol-name (process-status p))))
              (abbreviate-file-name (directory-file-name (eam-terminal-directory s)))
              (if (derived-mode-p 'eam-terminal-draft-mode)
                  " | C-c C-c transfer; C-c C-z CLI → Enter" " | M-x eam-help")))))
(defvar eam-terminal-controls-mode-map (make-sparse-keymap))
(define-minor-mode eam-terminal-controls-mode
  "Compatibility mode for existing terminal buffers; adds no GUI menu."
  :init-value nil :lighter nil :keymap eam-terminal-controls-mode-map)
(defvar eam-terminal-draft-mode-map
  (let ((map (make-sparse-keymap)))
    (define-key map (kbd "C-c C-c") #'eam-paste)
    (define-key map (kbd "C-c C-z") #'eam-focus)
    map))
(define-derived-mode eam-terminal-draft-mode text-mode "AI-draft"
  "Edit a draft. Transfer and Enter are separate explicit actions."
  (setq header-line-format "DRAFT | C-c C-c transfer | C-c C-z CLI → Enter | C-c C-z terminal")
  (buffer-enable-undo))
(defun eam-terminal--record (s kind text)
  (when (eam-terminal-input-file s)
    (let ((coding-system-for-write 'utf-8-unix))
    (write-region (concat (json-encode `((kind . ,kind) (text . ,text))) "\n")
                  nil (eam-terminal-input-file s) t 'silent))))
(defun eam-terminal--filter (s process bytes)
  "Pass output to Ghostel, optionally recording raw diagnostic bytes first."
  (condition-case err
      (progn
         (let ((coding-system-for-write 'no-conversion))
          (when (eam-terminal-file s)
            (write-region bytes nil (eam-terminal-file s) t 'silent)))
        (ghostel--filter process bytes))
    (error (setf (eam-terminal-error s) (error-message-string err))
           (eam-terminal--stop s)
           (message "Terminal recording failed: %s" (error-message-string err)))))
(defun eam-terminal--stop (s)
  (when (process-live-p (eam-terminal-process s))
    (delete-process (eam-terminal-process s))))
(defun eam-terminal--close ()
  (let ((s eam-terminal--current)
        (temporary (and (buffer-live-p (eam-terminal-output eam-terminal--current))
                        (buffer-local-value 'eam-terminal-temporary
                                            (eam-terminal-output eam-terminal--current))))
        (persistent eam-terminal-persistent-directory)
        (state (and (boundp 'eam-persistent--display-state)
                    (buffer-live-p (eam-terminal-output eam-terminal--current))
                    (buffer-local-value 'eam-persistent--display-state
                                        (eam-terminal-output eam-terminal--current)))))
    (eam-terminal--stop s)
    (setq eam-terminal--sessions (delq s eam-terminal--sessions))
    (dolist (b (list (eam-terminal-input s) (eam-terminal-output s)))
      (when (buffer-live-p b)
        (with-current-buffer b (set-buffer-modified-p nil))
        (kill-buffer b)))
    (when persistent
      (if temporary
          (message "Quick session closed; CLI is stopping")
        (if state
          (message "Session display closed; %s. Disk records retained." state)
        (message "Session detached; CLI continues running. Use M-x eam-quit to end it."))))))
(defun eam-terminal--detach ()
  (when eam-terminal--current
    (eam-terminal--stop eam-terminal--current)
    (setq eam-terminal--sessions
          (delq eam-terminal--current eam-terminal--sessions))))
(defun eam-focus ()
  (interactive) (pop-to-buffer (eam-terminal-output eam-terminal--current)))
(defun eam-terminal--draft-name (s)
  "Keep the parent terminal's disambiguating suffix when naming its draft."
  (let ((parent (buffer-name (eam-terminal-output s)))
        (prefix (format "*%s terminal:" (eam-terminal-name s))))
    (if (string-prefix-p prefix parent)
        (concat (format "*%s draft:" (eam-terminal-name s))
                (substring parent (length prefix)))
      (format "*Draft for %s*" parent))))
(defun eam-terminal--ensure-draft (s)
  (unless (buffer-live-p (eam-terminal-input s))
    (let ((b (generate-new-buffer
              (eam-terminal--draft-name s))))
      (setf (eam-terminal-input s) b)
      (with-current-buffer b
        (eam-terminal-draft-mode)
        (setq-local eam-terminal--current s)
        (setq-local header-line-format '(:eval (eam-terminal--header)))
        (setq default-directory (buffer-local-value 'default-directory (eam-terminal-output s)))
        (eam-terminal-controls-mode 1))))
  (eam-terminal-input s))
(defun eam-draft ()
  "Open the optional draft; keep the running terminal visible."
  (interactive)
  (pop-to-buffer (eam-terminal--ensure-draft eam-terminal--current)))
(defun eam-draft-add-selection ()
  "Copy selected terminal text to the end of the optional draft."
  (interactive)
  (unless (and (derived-mode-p 'ghostel-mode) (use-region-p))
    (user-error "Select terminal text first (Ghostel copy mode or mouse)"))
  (let* ((s eam-terminal--current)
         (text (filter-buffer-substring (region-beginning) (region-end))))
    (with-current-buffer (eam-terminal--ensure-draft s)
      (goto-char (point-max))
      (undo-boundary)
      (unless (bolp) (insert "\n"))
      (insert text)
      (undo-boundary))
    (eam-draft)))
(defvar eam-terminal--temporary-socket-directory nil)
(defcustom eam-emacsclient-executable nil
  "Optional emacsclient path; nil discovers it beside this Emacs or on PATH."
  :type '(choice (const nil) file) :group 'eam)
(defun eam-terminal--emacsclient ()
  "Find the editor client before starting a local server."
  (or (if eam-emacsclient-executable
          (let ((path (expand-file-name eam-emacsclient-executable)))
            (and (file-regular-p path) (file-executable-p path) path))
        (cl-find-if
         (lambda (path) (and path (file-regular-p path) (file-executable-p path)))
         (list (expand-file-name "bin/emacsclient" invocation-directory)
               (expand-file-name "emacsclient" invocation-directory)
               (executable-find "emacsclient"))))
      (user-error "emacsclient not found; set eam-emacsclient-executable")))
(defun eam-terminal--clean-socket-directory ()
  "Remove only this instance's empty temporary socket directory."
  (when eam-terminal--temporary-socket-directory
    (condition-case nil
        (progn (delete-directory eam-terminal--temporary-socket-directory)
               (setq eam-terminal--temporary-socket-directory nil))
      (file-error nil))))
(defvar eam-terminal--editor-files (make-hash-table :test #'equal))
(defvar-local eam-terminal--editor-old-header nil)
(defvar-local eam-terminal--editor-original nil)
(defvar eam-terminal-editor-mode-map
  (let ((map (make-sparse-keymap)))
    (define-key map (kbd "C-c C-c") #'eam-input-commit)
    (define-key map (kbd "C-c C-k") #'eam-input-cancel)
    map))
(define-minor-mode eam-terminal-editor-mode
  "Local CLI editor keys: commit or cancel the current edit."
  :lighter " CLI-edit" :keymap eam-terminal-editor-mode-map)
(defun eam-input-commit ()
  "Save this CLI input and return it without submitting an AI request."
  (interactive)
  (unless (and eam-terminal-editor-mode server-buffer-clients)
    (user-error "Use a CLI input editing buffer"))
  (let ((require-final-newline nil)) (save-buffer))
  (server-edit))
(defun eam-input-cancel ()
  "Restore the input from before this edit and return it to the CLI.
Also undo intermediate saves made during this editing visit."
  (interactive)
  (unless (and eam-terminal-editor-mode server-buffer-clients)
    (user-error "Use a CLI input editing buffer"))
  (widen)
  (erase-buffer)
  (insert eam-terminal--editor-original)
  (let ((require-final-newline nil)) (save-buffer))
  (server-edit))
(dolist (command '(eam-input-commit eam-input-cancel eam-terminal-editor-mode))
  (put command 'completion-predicate #'ignore))
(defun eam-terminal--register-editor-files (files)
  "Register explicit CLI editor FILES for one visit, expiring after a minute."
  (maphash (lambda (file deadline)
             (when (< deadline (float-time)) (remhash file eam-terminal--editor-files)))
           eam-terminal--editor-files)
  (dolist (file files)
    (puthash (expand-file-name file) (+ (float-time) 60) eam-terminal--editor-files)))
(defun eam-terminal--guide-editor-file ()
  "Add instructions to a registered external edit, without changing its text."
  (when-let* ((file buffer-file-name)
              (deadline (gethash file eam-terminal--editor-files)))
    (remhash file eam-terminal--editor-files)
    (when (> deadline (float-time))
      (setq-local eam-terminal--editor-old-header header-line-format)
      (setq-local eam-terminal--editor-original
                  (save-restriction (widen) (buffer-substring-no-properties (point-min) (point-max))))
      (eam-terminal-editor-mode 1)
      (setq-local header-line-format
                  "CLI 입력 편집 | C-c C-c 반영·반환 | C-c C-k 취소·반환 | CLI에서 Enter 제출")
      (add-hook 'server-done-hook #'eam-terminal--clear-editor-guide nil t))))
(defun eam-terminal--clear-editor-guide ()
  "Restore the previous header when the external edit finishes."
  (eam-terminal-editor-mode -1)
  (setq eam-terminal--editor-original nil)
  (setq-local header-line-format eam-terminal--editor-old-header)
  (remove-hook 'server-done-hook #'eam-terminal--clear-editor-guide t))
(add-hook 'server-visit-hook #'eam-terminal--guide-editor-file)

(defun eam-terminal--editor-command ()
  "Return an emacsclient command targeting this Emacs, without a new frame."
  (let ((client (eam-terminal--emacsclient)))
  (unless (process-live-p server-process)
    (setq server-name (format "eam-%d" (emacs-pid))
          server-socket-dir (expand-file-name "editor-server" eam-directory))
    ;; macOS AF_UNIX paths have a small byte limit; long project paths and
    ;; multibyte names must not prevent the external editor from connecting.
    (when (>= (string-bytes (expand-file-name server-name server-socket-dir)) 100)
      (eam-terminal--clean-socket-directory)
      (setq server-socket-dir (make-temp-file "/tmp/eam-editor-" t)
            eam-terminal--temporary-socket-directory server-socket-dir))
    (condition-case err
        (progn
          (make-directory server-socket-dir t)
          (set-file-modes server-socket-dir #o700)
          (server-start)
          (add-hook 'kill-emacs-hook #'eam-terminal--clean-socket-directory t))
      (error
       ;; Startup can fail before an exit hook is installed. Only remove our
       ;; empty fallback directory; never delete another server's socket.
       (eam-terminal--clean-socket-directory)
       (signal (car err) (cdr err)))))
  (when server-use-tcp (user-error "This experiment requires a local Unix Emacs server"))
  (mapconcat #'shell-quote-argument
             (list (eam-native--executable) "editor" "--direct" client (expand-file-name server-name server-socket-dir)) " ")))
(defun eam-edit-input ()
  "Ask the native CLI to open its current input in this Emacs."
  (interactive)
  (eam-terminal-key "g" "ctrl")
  (message "CLI editor: edit the opened file, then C-c C-c to commit or C-c C-k to cancel"))
(defun eam-terminal--alive (s)
  (unless (and (buffer-live-p (eam-terminal-output s))
               (process-live-p (eam-terminal-process s)))
    (user-error "CLI is not running")))
(defun eam-history ()
  "Open the current session's identified native conversation without a picker."
  (interactive)
  (require 'eam-native-history)
  (require 'eam-persistent)
  (eam-native-history-current (eam-session-context-directory)))
(defun eam-paste ()
  "Copy the draft to the CLI, without pressing Enter or clearing either input."
  (interactive)
  (let* ((s eam-terminal--current)
         (text (with-current-buffer (eam-terminal--ensure-draft s) (buffer-string))))
    (eam-terminal--alive s)
    (when (or (string-empty-p text) (> (string-bytes text) 65536))
      (user-error "Draft must contain 1 byte to 64 KiB"))
    (when (string-match-p "[\x00-\x08\x0b-\x1f\x7f]" text)
      (user-error "Draft contains terminal control characters"))
    (when (and (string-match-p "\n" text) (not (with-current-buffer (eam-terminal-output s)
                   (ghostel--mode-enabled ghostel--term 2004))))
      (user-error "CLI has not enabled bracketed paste; multiline transfer blocked"))
    (eam-terminal--alive s)
    (eam-terminal--record s "draft-transfer" text)
    (with-current-buffer (eam-terminal-output s) (ghostel-paste-string text))
    (message "Transferred; inspect CLI, then Enter in the CLI to submit")))
(defun eam-terminal-key (key &optional mods)
  (let ((s eam-terminal--current))
    (eam-terminal--alive s)
    (eam-terminal--record s "key" (concat (or mods "") ":" key))
    (with-current-buffer (eam-terminal-output s) (ghostel-send-key key mods))))
(defun eam-terminal--window-buffer-change (window)
  "Preserve WINDOW's restored scroll position when displaying a session.
Only re-anchor a window that is already following live terminal output."
  (when (and (window-live-p window)
             (eq (window-buffer window) (current-buffer))
             (ghostel--window-anchored-p window))
    (ghostel--anchor-window window)))

(defun eam-terminal--defer-synchronized-redraw (buffer)
  "Defer BUFFER's unfinished synchronized frame before window postprocessing.
Forced redraws still bypass this guard, as they do in Ghostel's renderer."
  (with-current-buffer buffer
    (and ghostel--term
         (not ghostel--force-next-redraw)
         (ghostel--mode-enabled ghostel--term 2026))))

(defun eam-terminal-start (name executable args directory &optional reuse)
  "Start an interactive CLI, or reconnect the disconnected session REUSE.
REUSE retains the output buffer identity and the separate draft."
  (when (and reuse (or (not (buffer-live-p (eam-terminal-output reuse)))
                       (process-live-p (eam-terminal-process reuse))))
    (user-error "Only a disconnected live buffer can be reused"))
  (make-directory eam-directory t)
  (let* ((file (and (not reuse) eam-record-terminal (make-temp-file (expand-file-name "terminal-" eam-directory) nil ".ansi")))
         (project-directory (file-name-as-directory (expand-file-name directory)))
         (s (or reuse (eam-terminal--session
             :name name :directory project-directory
             :file file :input-file (and file (concat file ".input.jsonl"))
             :output (generate-new-buffer (eam-terminal--buffer-name name project-directory)))))
         (default-directory (file-name-as-directory (expand-file-name directory)))
         (process-environment (copy-sequence process-environment))
         started)
    (unwind-protect
        (progn
          (when (and (not reuse) (eam-terminal-input-file s))
             (let ((coding-system-for-write 'utf-8-unix))
              (write-region "" nil (eam-terminal-input-file s) nil 'silent))
            (set-file-modes (eam-terminal-input-file s) #o600))
          (let ((editor (eam-terminal--editor-command)))
            (setenv "EDITOR" editor)
            (setenv "VISUAL" editor))
          ;; Keep native CLI configuration and slash commands. No prompt wrapper.
          (dolist (key '("ANTHROPIC_API_KEY" "ANTHROPIC_AUTH_TOKEN" "OPENAI_API_KEY" "CODEX_API_KEY"))
            (setenv key nil))
          (with-current-buffer (eam-terminal-output s)
            (setq default-directory (file-name-as-directory (expand-file-name directory)))
            ;; Emacs owns the PTY so every received byte can be archived.
            (let ((ghostel-use-native-pty nil)
                  (ghostel-max-scrollback (* 5 1024 1024))
                  (ghostel-timer-delay eam-terminal-redraw-delay)
                  (ghostel-shell-integration nil)
                  (ghostel-module-auto-install nil))
              (setf (eam-terminal-process s) (ghostel-exec (current-buffer) executable args)))
            (setq-local ghostel-timer-delay eam-terminal-redraw-delay)
            (add-hook 'ghostel-inhibit-redraw-functions
                      #'eam-terminal--defer-synchronized-redraw nil t)
            ;; Ghostel's default display hook unconditionally follows the
            ;; CLI cursor in char/semi-char mode, losing restored scrollback.
            (remove-hook 'window-buffer-change-functions
                         #'ghostel--window-buffer-change t)
            (add-hook 'window-buffer-change-functions
                      #'eam-terminal--window-buffer-change nil t)
            (setq-local ghostel-kill-buffer-on-exit nil)
            (setq-local ghostel-notification-function
                        (lambda (title body)
                          (eam-notifications--receive (eam-terminal-output s)
                                                           title body)))
            (setq-local eam-terminal--current s)
            (setq-local header-line-format '(:eval (eam-terminal--header)))
            (buffer-disable-undo)
            (eam-terminal-controls-mode 1)
            (add-hook 'kill-buffer-hook #'eam-terminal--detach nil t)
            (set-process-filter (eam-terminal-process s)
                                (lambda (proc bytes) (eam-terminal--filter s proc bytes))))
          (cl-pushnew s eam-terminal--sessions)
          (pop-to-buffer (eam-terminal-output s))
          (setq started t)
          s)
      (unless started
        (setq eam-terminal--sessions (delq s eam-terminal--sessions))
        ;; ghostel-exec may create a PTY and then fail before returning it.
        (let* ((buffer (eam-terminal-output s))
               (process (or (eam-terminal-process s)
                            (and (buffer-live-p buffer) (get-buffer-process buffer)))))
          (when (and process (process-live-p process)) (delete-process process)))
        (dolist (buffer (unless reuse (list (eam-terminal-input s) (eam-terminal-output s))))
          (when (buffer-live-p buffer)
            (with-current-buffer buffer
              (let ((kill-buffer-query-functions nil))
                (set-buffer-modified-p nil)
                (kill-buffer buffer)))))))))
(defun eam-terminal--read-project-directory ()
  "Read a project directory, offering to create a missing local directory."
  (let* ((directory (expand-file-name
                     (read-directory-name "Project directory: " default-directory
                                          default-directory nil)))
         (path (directory-file-name directory)))
    (when (file-remote-p directory)
      (user-error "EAM sessions require a local project directory"))
    (unless (file-directory-p directory)
      (when (or (file-exists-p path) (file-symlink-p path))
        (user-error "Path exists but is not a directory: %s" path))
      (unless (yes-or-no-p (format "Create project directory %s? " directory))
        (user-error "Session creation cancelled"))
      (make-directory directory t))
    (file-name-as-directory directory)))

(defun eam-terminal-start-provider (provider directory &optional name)
  "Start PROVIDER as a persistent CLI in DIRECTORY without submitting input."
  (setq provider (eam--provider-name provider))
  (require 'eam-persistent)
  (if name (eam-persistent--start provider directory nil name)
    (eam-persistent--start provider directory nil)))
;;;###autoload
(defun eam-resume (provider directory &optional id name)
  "Open the saved conversation picker for PROVIDER in DIRECTORY.
Optional ID is a UUID for programmatic callers; NAME labels a new session.
Known live resumes in this Emacs are selected instead of opened twice."
  (interactive
   (let ((use-dialog-box nil))
     (list (eam--read-provider)
           (eam-terminal--read-project-directory) nil
           (read-string "Session name (optional): "))))
  (unless (member provider '("Claude" "Codex")) (user-error "Choose Claude or Codex"))
  (setq id (downcase (string-trim (or id ""))))
  (unless (or (string-empty-p id)
              (string-match-p
               "\\`[0-9a-f]\\{8\\}-[0-9a-f]\\{4\\}-[0-9a-f]\\{4\\}-[0-9a-f]\\{4\\}-[0-9a-f]\\{12\\}\\'" id))
    (user-error "Use a conversation UUID, or leave empty for the native list"))
  (let ((existing
         (and (not (string-empty-p id))
              (cl-find-if
               (lambda (s) (and (equal provider (eam-terminal-name s))
                                (equal id (eam-terminal-resume-id s))
                                (buffer-live-p (eam-terminal-output s))
                                (process-live-p (eam-terminal-process s))))
               eam-terminal--sessions))))
    (if existing
        (progn (pop-to-buffer (eam-terminal-output existing)) existing)
      (require 'eam-persistent)
      (let ((s (apply #'eam-persistent--start
                provider directory
                (append (if (equal provider "Claude") '("--resume") '("resume"))
                        (unless (string-empty-p id) (list id)))
                (when name (list name)))))
        (setf (eam-terminal-resume-id s) (unless (string-empty-p id) id))
        s))))
;;;###autoload
(dolist (command '(eam-focus eam-draft-add-selection eam-terminal-controls-mode eam-terminal-draft-mode))
  (put command 'completion-predicate #'ignore))

(provide 'eam-terminal)
