;;; eam-native-history.el --- Read native CLI conversations -*- lexical-binding: t; -*-
(require 'eam)
(require 'eam-native)
(require 'json)
(defcustom eam-claude-history-directory
  (file-name-as-directory (or (getenv "CLAUDE_CONFIG_DIR") (expand-file-name "~/.claude")))
  "Claude configuration root containing projects/." :type 'directory :group 'eam)
(defcustom eam-codex-history-directory
  (file-name-as-directory (or (getenv "CODEX_HOME") (expand-file-name "~/.codex")))
  "Codex configuration root containing native history databases." :type 'directory :group 'eam)
(defvar-local eam-native-history--entry nil)
(defvar-local eam-native-history--start 0)
(defvar-local eam-native-history--end 0)
(defvar-local eam-native-history--prompt nil)

(defun eam-native-history--query (request)
  "Read local native history with a cancellable worker and bounded result."
  (let ((buffer (generate-new-buffer " *eam-native-result*")) process
        (deadline (+ (float-time) 30)) too-large)
    (unwind-protect
        (progn
          (setq process
                (make-process
                 :name "eam-native-history" :buffer buffer :noquery t
                 :connection-type 'pipe :coding 'utf-8-unix
                 :command (list (eam-native--executable) "history")
                 :sentinel #'ignore
                 :filter (lambda (proc text)
                           (with-current-buffer buffer
                             (if (> (+ (buffer-size) (length text)) (* 2 1024 1024))
                                 (progn (setq too-large t) (delete-process proc))
                               (goto-char (point-max)) (insert text))))))
          (process-send-string process (json-encode request))
          (process-send-eof process)
          (while (and (process-live-p process) (< (float-time) deadline))
            (accept-process-output process .025))
          (when (process-live-p process) (user-error "Native history query timed out"))
          (when too-large (user-error "Native history result exceeded 2 MiB"))
          (with-current-buffer buffer
            (goto-char (point-min))
            (let ((result (json-parse-buffer :object-type 'alist :array-type 'list :null-object nil :false-object nil)))
              (when (alist-get 'error result) (user-error "Native history: %s" (alist-get 'error result)))
              (unless (zerop (process-exit-status process)) (user-error "Native history reader failed"))
              result)))
      (when (and process (process-live-p process)) (delete-process process))
      (kill-buffer buffer))))

(defvar eam-native-history-mode-map
  (let ((map (make-sparse-keymap)))
    (set-keymap-parent map special-mode-map)
    (define-key map (kbd "p") #'eam-native-history-previous)
    (define-key map (kbd "n") #'eam-native-history-next)
    (define-key map (kbd "g") #'eam-native-history-latest)
    (define-key map (kbd "q") #'quit-window)
    map))
(define-derived-mode eam-native-history-mode special-mode "EAM-history"
  "Read-only native conversation. p/n pages, g latest snapshot, q close."
  (buffer-disable-undo)
  (setq-local truncate-lines nil)
  (setq-local word-wrap t)
  (setq-local font-lock-defaults
              '((("^## .*" . font-lock-function-name-face)
                 ("^```.*" . font-lock-comment-face)
                 ("`[^`\n]+`" . font-lock-constant-face)) t))
  (font-lock-mode 1))

(defun eam-native-history--page (offset)
  (let* ((result (eam-native-history--query
                  `((action . "page") (entry . ,eam-native-history--entry)
                    (offset . ,offset) (limit . ,(min 65536 (eam--limit)))
                    (prompt . ,(if eam-native-history--prompt t :json-false)))))
         (text (alist-get 'text result))
         (inhibit-read-only t))
    (unless (and (stringp text) (<= (length text) (eam--limit)))
      (user-error "Invalid native history page"))
    (erase-buffer) (insert text) (goto-char (point-min))
    (setq eam-native-history--start (alist-get 'start result)
          eam-native-history--end (alist-get 'end result)
          header-line-format
          (format "%s | %s | %d–%d / %d chars | p/n pages · g refresh · q close"
                  (if eam-native-history--prompt "최근 저장된 사용자 요청 (진행 여부 미판정)" "CLI 저장 대화")
                  (alist-get 'id eam-native-history--entry)
                  eam-native-history--start eam-native-history--end (alist-get 'total result)))))
(defun eam-native-history-previous ()
  (interactive)
  (eam-native-history--page (max 0 (- eam-native-history--start (min 65536 (eam--limit))))))
(defun eam-native-history-next ()
  (interactive) (eam-native-history--page eam-native-history--end))
(defun eam-native-history-latest ()
  (interactive) (eam-native-history--page nil))

(defun eam-native-history--show (entry &optional prompt)
  "Display ENTRY directly with the usual bounded history pages."
  (let ((buffer (get-buffer-create
                 (format "*EAM %s: %s*" (if prompt "prompt" "history")
                         (alist-get 'id entry)))))
    (with-current-buffer buffer
      (eam-native-history-mode)
      (setq eam-native-history--entry entry eam-native-history--prompt prompt)
      (eam-native-history-latest))
    (pop-to-buffer buffer)))

(defun eam-native-history-current (session)
  "Open SESSION's identified conversation without a picker or recency guess."
  (let ((entry (alist-get 'entry
                         (eam-native-history--query
                          `((action . "current") (session . ,session))))))
    (unless entry (user-error "Current conversation is not identified yet"))
    (eam-native-history--show entry)))

(defun eam-native-history-open (&optional provider directory prompt)
  "Choose an exact native conversation; never infer identity from recency."
  (setq provider (or provider (eam--read-provider)))
  (let* ((result (eam-native-history--query
                  `((action . "list") (provider . ,provider) (directory . ,directory)
                    (root . ,(expand-file-name (if (equal provider "Claude")
                                                  eam-claude-history-directory eam-codex-history-directory))))))
         (candidates
          (mapcar (lambda (entry)
                    (cons (format "%s | %s | %s | %s"
                                  (format-time-string "%Y-%m-%d %H:%M" (seconds-to-time (alist-get 'updated entry)))
                                  (replace-regexp-in-string "[\n\r\t]" " " (alist-get 'title entry))
                                  (alist-get 'directory entry) (alist-get 'id entry)) entry))
                  (alist-get 'entries result))))
    (unless candidates (user-error "No native %s conversations found for this project" provider))
    (let* ((choice (completing-read
                    (if (alist-get 'truncated result) "Saved conversation (latest 300): " "Saved conversation: ")
                    candidates nil t))
           (entry (cdr (assoc choice candidates))))
      (eam-native-history--show entry prompt))))
(dolist (command '(eam-native-history-mode eam-native-history-next eam-native-history-previous eam-native-history-latest))
  (put command 'completion-predicate #'ignore))
(provide 'eam-native-history)
