;;; eam-native.el --- Build and cache EAM's native helper -*- lexical-binding: t; -*-
(require 'eam)
(require 'compile)
(defcustom eam-native-executable nil
  "Optional prebuilt eam-runtime executable.  Nil builds the bundled source."
  :type '(choice (const nil) file) :group 'eam)
(defcustom eam-native-auto-build t
  "Build a missing native helper on first use, never during package loading."
  :type 'boolean :group 'eam)
(defcustom eam-native-cargo-executable "cargo"
  "Cargo executable used for local builds.  Rust must already be installed."
  :type 'string :group 'eam)
(defvar eam-native--verified (make-hash-table :test #'equal))
(defun eam-native--verify (binary)
  "Verify the helper protocol once per executable file revision."
  (let* ((attrs (file-attributes binary))
         (stamp (list (file-attribute-size attrs) (file-attribute-modification-time attrs))))
    (unless (equal stamp (gethash binary eam-native--verified))
      (with-temp-buffer
        (unless (and (eq 0 (call-process binary nil t nil "--version"))
                     (string-match-p "\\`eam-runtime [^\n]+ protocol=1\n?\\'" (buffer-string)))
          (user-error "Incompatible EAM native helper: %s" binary)))
      (puthash binary stamp eam-native--verified))
    binary))
(defun eam-native--source ()
  (expand-file-name "native/" eam--resource-directory))
(defun eam-native--cache ()
  "Return a platform and source-specific target directory outside the package."
  (let* ((source (eam-native--source))
         (files (append (list (expand-file-name "Cargo.toml" source)
                              (expand-file-name "Cargo.lock" source))
                        (sort (directory-files-recursively (expand-file-name "src" source) "\\.rs\\'")
                              #'string<)
                        (sort (directory-files-recursively (expand-file-name "web" source) ".")
                              #'string<)))
         (digest (with-temp-buffer
                   (set-buffer-multibyte nil)
                   (dolist (file files)
                     (insert (file-relative-name file source) "\0")
                     (insert-file-contents-literally file)
                     (goto-char (point-max)) (insert "\0"))
                   (secure-hash 'sha256 (current-buffer)))))
    (expand-file-name (concat "native/" system-configuration "/" digest "/") eam-directory)))
(defvar eam-native--build-process nil)
(defun eam-native--build-timeout (process)
  "Stop a build that exceeded its deadline."
  (when (process-live-p process)
    (process-put process 'eam-timeout t)
    (delete-process process)))
;;;###autoload
(defun eam-native-build ()
  "Build the runtime asynchronously and display its live compilation log.
Return the build process.  Use C-c C-k in *EAM native build* to cancel.
After completion, run the desired EAM command again."
  (interactive)
  (unless (memq system-type '(darwin gnu/linux))
    (user-error "EAM native PTY currently supports macOS/Linux"))
  (if (process-live-p eam-native--build-process)
      (progn (display-buffer (process-buffer eam-native--build-process))
             eam-native--build-process)
    (let* ((cargo (or (executable-find eam-native-cargo-executable)
                      (and (equal eam-native-cargo-executable "cargo")
                           (file-executable-p (expand-file-name "~/.cargo/bin/cargo"))
                           (expand-file-name "~/.cargo/bin/cargo"))
                      (user-error "Install Rust/Cargo and C build tools, then run M-x eam-native-build")))
           (process-environment (copy-sequence process-environment))
           (target (eam-native--cache))
           (binary (expand-file-name "release/eam-runtime" target))
           (default-directory (eam-native--source))
           (compilation-scroll-output t))
      (setenv "PATH" (concat (file-name-directory cargo) path-separator (or (getenv "PATH") "")))
      (make-directory target t)
      (let* ((buffer (compilation-start
                      (mapconcat #'shell-quote-argument
                                 (list cargo "build" "--release" "--locked"
                                       "--manifest-path" (expand-file-name "Cargo.toml" default-directory)
                                       "--target-dir" target) " ")
                      'compilation-mode (lambda (_) "*EAM native build*")))
             (process (get-buffer-process buffer))
             (sentinel (process-sentinel process))
             (timer (run-at-time 900 nil #'eam-native--build-timeout process)))
        (setq eam-native--build-process process)
        (with-current-buffer buffer
          (setq-local compilation-scroll-output t)
          (setq-local header-line-format "EAM build | C-c C-k cancel | Completion: rerun your EAM command"))
        (set-process-query-on-exit-flag process nil)
        (set-process-sentinel
         process
         (lambda (proc event)
           (funcall sentinel proc event)
           (unless (process-live-p proc)
             (cancel-timer timer)
             (let ((result
                    (cond
                     ((process-get proc 'eam-timeout) "Build timed out")
                     ((and (eq (process-status proc) 'exit)
                           (zerop (process-exit-status proc)))
                      (condition-case err
                          (progn (eam-native--verify binary)
                                 "Ready; run your EAM command again")
                        (error (concat "Build output invalid: " (error-message-string err)))))
                     (t "Build failed or cancelled; retry M-x eam-native-build"))))
               (when (buffer-live-p (process-buffer proc))
                 (with-current-buffer (process-buffer proc)
                   (setq header-line-format (concat "EAM | " result))))
               (message "EAM: %s" result)))))
        (display-buffer buffer)
        (message "EAM: building in background; see *EAM native build* (C-c C-k cancels)")
        process))))
(defun eam-native--executable ()
  "Find the native helper, building on explicit first use if configured."
  (if eam-native-executable
      (or (and (file-name-absolute-p eam-native-executable)
               (file-executable-p eam-native-executable)
               (eam-native--verify eam-native-executable))
          (user-error "eam-native-executable must name an executable absolute path"))
    (let ((binary (expand-file-name "release/eam-runtime" (eam-native--cache))))
      (cond ((file-executable-p binary) (eam-native--verify binary))
            (eam-native-auto-build
             (eam-native-build)
             (user-error "EAM native build started; see *EAM native build*, then run this command again"))
            (t (user-error "Native helper missing; run M-x eam-native-build"))))))
(provide 'eam-native)
;;; eam-native.el ends here
