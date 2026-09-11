;;; eam-connect.el --- Local official CLI connections -*- lexical-binding: t; -*-
(require 'eam)
(require 'eam-native)
(require 'subr-x)
(defun eam--executable (name)
  (or (cl-find-if #'file-executable-p
                  (mapcar (lambda (dir) (expand-file-name name dir))
                          '("~/.local/bin" "/opt/homebrew/bin" "/usr/local/bin")))
      (executable-find name)
      (user-error "Executable not found: %s" name)))
(defun eam-connect-new (provider &optional directory)
  "Create an idle connection. No model request until Send input."
  (let* ((directory (file-name-as-directory
                     (expand-file-name
                      (or directory (read-directory-name "Project directory: "
                                                         eam--resource-directory)))))
         (s (eam--create)))
    (setf (eam-session-provider s) provider
          (eam-session-directory s) directory
          (eam-session-state-file s) (concat (eam-session-file s) ".provider.json")
          (eam-session-status s) 'idle)
    (dolist (b (list (eam-session-input s) (eam-session-output s)))
      (with-current-buffer b
        (setq default-directory directory)
        (rename-buffer (format "*AI %s %s*" provider
                               (if (eq b (eam-session-input s)) "input" "output")) t)))
    (with-current-buffer (eam-session-output s) (eam-history-latest))
    (pop-to-buffer (eam-session-output s))
    (display-buffer (eam-session-input s) '(display-buffer-pop-up-window))
    (pop-to-buffer (eam-session-input s))
    s))

(defun eam-connect--filter (session chunk)
  "Persist received decoded output immediately; rendering happens on a timer."
  (setf (eam-session-status session) 'streaming)
  (let ((coding-system-for-write 'utf-8-unix))
    (write-region chunk nil (eam-session-file session) t 'silent)))

(defun eam-connect--flush (session)
  "Project new disk output without a growing in-memory queue."
  (condition-case err
      (when (buffer-live-p (eam-session-output session))
        (let* ((end (eam--size session))
               (offset (or (eam-session-display-offset session) end))
               (start (max offset (- end (- (eam--limit) 4)))))
          (unless (eam-session-cursor session)
            (when (> end offset)
              (when (> start offset)
                ;; A burst skipped the old viewport. Replace it; never join a gap.
                (with-current-buffer (eam-session-output session)
                  (let ((inhibit-read-only t)) (erase-buffer))))
              (eam--display-append session (eam--read-text session start end))))
          (setf (eam-session-display-offset session) end)))
    (error
     (setf (eam-session-error session) (error-message-string err))
     (eam--cancel session)
     (message "AI output stopped: %s" (error-message-string err)))))

(defun eam-connect--sentinel (session process _event)
  (when (memq (process-status process) '(exit signal failed))
    (when (timerp (eam-session-timer session))
      (cancel-timer (eam-session-timer session)))
    (setf (eam-session-timer session) nil
          (eam-session-status session)
          (if (= 0 (process-exit-status process)) 'completed 'stopped))
    (eam-connect--flush session)
    (when (buffer-live-p (eam-session-output session))
      (with-current-buffer (eam-session-output session)
        (unless (eam-session-cursor session) (eam--live-header session))))))

(defun eam-connect-send ()
  (let* ((s eam--current)
         (text (buffer-substring-no-properties (point-min) (point-max)))
         (provider (symbol-name (eam-session-provider s)))
         (executable (eam--executable provider))
         (native (eam-native--executable))
         (default-directory (eam-session-directory s)))
    (when (eam-session-archived s) (user-error "Archive is read-only"))
    (unless (buffer-live-p (eam-session-output s)) (user-error "Output buffer is closed"))
    (when (process-live-p (eam-session-process s)) (user-error "Response active; stop it first"))
    (when (or (string-empty-p text) (> (string-bytes (encode-coding-string text 'utf-8-unix)) (* 1024 1024)))
      (user-error "Input must contain 1 byte to 1 MiB of UTF-8 text"))
    (eam--append s (concat "\n[사용자]\n" text "\n[" provider "]\n"))
    (setf (eam-session-display-offset s) (eam--size s)
          (eam-session-status s) 'connecting
          (eam-session-error s) nil)
    (with-current-buffer (eam-session-output s)
      (unless (eam-session-cursor s) (eam--live-header s)))
    (let* ((prefix (make-temp-name (concat (eam-session-file s) ".turn-")))
           (stderr (make-pipe-process
                    :name (concat "ai-stderr-" provider) :buffer nil :noquery t
                    :filter (lambda (_p chunk)
                              (let ((coding-system-for-write 'utf-8-unix))
                                (write-region chunk nil (concat prefix ".bridge-stderr.log") t 'silent)))))
           (process
            (make-process
             :name (concat "ai-" provider) :buffer nil :noquery t
             :connection-type 'pipe :coding '(utf-8-unix . utf-8-unix) :stderr stderr
             :command (list native "provider" "--provider" provider "--executable" executable
                            "--cwd" default-directory "--state" (eam-session-state-file s) "--prefix" prefix)
             :filter (lambda (_p chunk)
                       (condition-case err
                           (eam-connect--filter s chunk)
                         (error (setf (eam-session-error s) (error-message-string err))
                                (eam--cancel s))))
             :sentinel (lambda (p event) (eam-connect--sentinel s p event)))))
      (setf (eam-session-process s) process
            (eam-session-stderr-process s) stderr
            (eam-session-timer s)
            (run-at-time eam-interval eam-interval #'eam-connect--flush s))
      (process-send-string process text)
      (process-send-eof process))))

(provide 'eam-connect)
;;; eam-connect.el ends here
