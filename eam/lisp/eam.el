;;; eam.el --- Bounded buffer streaming UI -*- lexical-binding: t; -*-
(declare-function eam-native-history-open "eam-native-history" (&optional provider directory prompt))

;; Version: 0.1.0
;; Package-Requires: ((emacs "30.1") (ghostel "0.53.0"))
;; Keywords: tools, terminals

(require 'cl-lib)
(require 'subr-x)
(require 'ansi-color)
(autoload 'eam-history-search "eam-history" nil t)
(autoload 'eam-history-next "eam-history" nil t)
(autoload 'eam-history-toggle-colors "eam-history" nil t)
(defvar-local eam-history-hide-colors t)
(declare-function eam-connect-new "eam-connect" (provider &optional directory))
(declare-function eam-connect-send "eam-connect" ())

(defgroup eam nil "EAM Management." :group 'applications)
(defconst eam--providers '("Claude" "Codex"))
(defvar eam--provider-history nil)
(defun eam--provider-name (provider)
  "Validate PROVIDER and return its canonical name."
  (or (car (member-ignore-case provider eam--providers))
      (user-error "Choose a supported provider: %s" (string-join eam--providers ", "))))
(defun eam--read-provider ()
  "Choose a provider with completion; cancellation starts nothing."
  (let ((completion-ignore-case t))
    (eam--provider-name
     (completing-read "Provider: " eam--providers nil t nil 'eam--provider-history))))
(defconst eam--resource-directory
  (let ((directory (file-name-directory (or load-file-name buffer-file-name))))
    (if (equal (file-name-nondirectory (directory-file-name directory)) "lisp")
        (file-name-directory (directory-file-name directory))
      directory))
  "Root containing the installed Lisp, bridge scripts and documentation.")
(defcustom eam-directory
  (expand-file-name "eam/" user-emacs-directory)
  "Directory for complete transcripts, separate from installed package code.
Created on first use, not when loading the package.  Standalone launchers
explicitly select their project-local var directory instead."
  :type 'directory)
(defcustom eam-buffer-limit 65536 "Maximum output characters (minimum 128)." :type 'integer)
(defcustom eam-interval 0.05 "Seconds between batched output updates." :type 'number)
(cl-defstruct (eam-session (:constructor eam--session))
  file output input timer (remaining 0) (sequence 0) cursor error archived
  provider directory process stderr-process display-offset state-file status)
(defvar eam--sessions nil)
(defvar-local eam--current nil)

(defvar eam-output-mode-map
  (let ((map (make-sparse-keymap)))
    (set-keymap-parent map special-mode-map)
    (define-key map (kbd "p") #'eam-history-previous-page)
    (define-key map (kbd "n") #'eam-history-next-page)
    (define-key map (kbd "g") #'eam-history-latest)
    (define-key map (kbd "q") #'eam-detach)
    (define-key map (kbd "/") #'eam-history-search)
    (define-key map (kbd "M-n") #'eam-history-next)
    (define-key map (kbd "c") #'eam-history-toggle-colors)
    map))
(define-derived-mode eam-output-mode special-mode "AI-output"
  "Bounded output. p/n: pages, /: search, M-n: next hit, c: color codes, q: close."
  (buffer-disable-undo)
  (setq-local truncate-lines nil)
  (setq-local word-wrap t))
(defun eam--limit () (max 128 eam-buffer-limit))
(defun eam--size (session)
  (file-attribute-size (file-attributes (eam-session-file session))))
(defun eam--live-header (session)
  "Show persisted bytes separately from bounded display characters."
  (setq header-line-format
        (format "%s | saved %d B | shown %d chars | p history, / search"
                (if (eam-session-provider session)
                    (format "LIVE %s [%s]" (eam-session-provider session)
                            (or (eam-session-status session) "idle"))
                  (if (eam-session-archived session) "ARCHIVE" "LIVE"))
                (eam--size session) (buffer-size))))
(defun eam--append (session text)
  "Persist TEXT before displaying it; never retain an in-memory transcript."
  (let ((coding-system-for-write 'utf-8-unix))
    (write-region text nil (eam-session-file session) t 'silent))
  (eam--display-append session text))

(defun eam--display-append (session text)
  "Append TEXT to the bounded projection; caller has already persisted it."
  (when (and (null (eam-session-cursor session))
             (buffer-live-p (eam-session-output session)))
    (with-current-buffer (eam-session-output session)
      (let ((inhibit-read-only t)
            (at-end (= (point) (point-max)))
            (windows (mapcar (lambda (w) (cons w (= (window-point w) (point-max))))
                             (get-buffer-window-list (current-buffer) nil t))))
        (save-excursion
          (goto-char (point-max))
          ;; Bound transient display allocation even for a huge submitted prompt.
          (insert (if (> (length text) (eam--limit))
                      (substring text (- (length text) (eam--limit))) text))
          (when (> (buffer-size) (eam--limit))
            (delete-region (point-min) (- (point-max) (eam--limit)))))
        (when at-end (goto-char (point-max)))
        (dolist (entry windows)
          (when (cdr entry) (set-window-point (car entry) (point-max))))
        (eam--live-header session)))))

(defun eam--read-text (session start end)
  "Read a UTF-8 byte interval with whole-character boundaries."
  (with-temp-buffer
           (set-buffer-multibyte nil)
           (insert-file-contents-literally (eam-session-file session) nil
                                          start (min (+ end 3) (eam--size session)))
           (let ((a 0) (b (min (- end start) (buffer-size))))
             (while (and (< a (buffer-size))
                         (= (logand (char-after (+ 1 a)) #xc0) #x80))
               (setq a (1+ a)))
             (while (and (< b (buffer-size))
                         (= (logand (char-after (+ 1 b)) #xc0) #x80))
               (setq b (1+ b)))
             (decode-coding-string (buffer-substring-no-properties (1+ a) (1+ (max a b)))
                                   'utf-8-unix))))

(defun eam--readable-terminal-text (text)
  "Keep row boundaries when displaying terminal TEXT as a plain transcript.
Cursor rewrites become new lines; this does not reconstruct a terminal screen."
  (setq text (replace-regexp-in-string "\r\n" "\n" text t t))
  (setq text (replace-regexp-in-string "\r" "\n" text t t))
  (replace-regexp-in-string "\e\\[[0-9;]*[ABEFHfd]" "\n" text t t))

(defun eam--color-page (session start text)
  "Render TEXT with bounded preceding SGR context from SESSION at START.
At most 64 KiB of preceding bytes are read; older color state is unknown."
  (let ((ansi-color-context nil)
        (position (max 0 (- start 65536))))
    ;; Keep individual reads bounded too, including with small page limits.
    (while (< position start)
      (let ((end (min start (+ position (- (eam--limit) 4)))))
        (ansi-color-apply (eam--read-text session position end))
        (setq position end)))
    ;; A page may begin in a CSI sequence carried from the prefix.
    (when (cadr ansi-color-context)
      (setq text (concat (cadr ansi-color-context) text))
      (setcar (cdr ansi-color-context) ""))
    (let ((rendered (ansi-color-apply (eam--readable-terminal-text text))))
      ;; Display faces in special-mode without depending on font-lock setup.
      (let ((position 0))
        (while (< position (length rendered))
          (let ((end (next-single-property-change position 'font-lock-face rendered
                                                  (length rendered)))
                (face (get-text-property position 'font-lock-face rendered)))
            (when face (put-text-property position end 'face face rendered))
            (setq position end))))
      (substring rendered 0 (min (length rendered) (eam--limit))))))

(defun eam--page (session start end)
  "Read bounded UTF-8 byte interval, extending boundaries to whole characters."
  (let ((text (eam--read-text session start end)))
    (with-current-buffer (eam-session-output session)
      (let ((inhibit-read-only t))
        (erase-buffer)
        (insert (if eam-history-hide-colors
                    (eam--color-page session start text)
                  text))
        (goto-char (point-min))
        (if (eam-session-cursor session)
            (setq header-line-format
                  (format "HISTORY (frozen) | bytes %d–%d | p/n pages, / search, c colors"
                          start end))
          (setf (eam-session-display-offset session) end)
          (eam--live-header session))))))
(defun eam-history-previous-page ()
  (interactive)
  (let* ((s eam--current)
         (end (if (eam-session-cursor s) (car (eam-session-cursor s)) (eam--size s)))
         (start (max 0 (- end (- (eam--limit) 4)))))
    (setf (eam-session-cursor s) (cons start end))
    (eam--page s start end)))
(defun eam-history-next-page ()
  (interactive)
  (let* ((s eam--current)
         (start (if (eam-session-cursor s) (cdr (eam-session-cursor s)) (eam--size s)))
         (end (min (eam--size s) (+ start (- (eam--limit) 4)))))
    (if (>= start (eam--size s)) (eam-history-latest)
      (setf (eam-session-cursor s) (cons start end))
      (eam--page s start end))))
(defun eam-history-latest ()
  (interactive)
  (let* ((s eam--current) (end (eam--size s)))
    (setf (eam-session-cursor s) nil)
    (eam--page s (max 0 (- end (- (eam--limit) 4))) end)
    (goto-char (point-max))))

(defun eam--cancel (session)
  (when (process-live-p (eam-session-process session))
    (signal-process (eam-session-process session) 'SIGTERM))
  (when (timerp (eam-session-timer session))
    (cancel-timer (eam-session-timer session)))
  (setf (eam-session-timer session) nil
        (eam-session-remaining session) 0))
(defun eam--detach ()
  (when eam--current
    (eam--cancel eam--current)
    (setq eam--sessions (delq eam--current eam--sessions))))
;;;###autoload
(defun eam-detach ()
  "Close the current session display, retaining persistent CLI and records."
  (interactive)
  (cond
   ((bound-and-true-p eam-terminal--current)
    (eam-terminal--close))
   (eam--current (eam--close-buffer-session))
   (t (user-error "Use a session output or input buffer"))))
(defun eam--close-buffer-session ()
  (let ((s eam--current))
    (eam--cancel s)
    (dolist (b (list (eam-session-input s) (eam-session-output s)))
      (when (buffer-live-p b)
        (with-current-buffer b (set-buffer-modified-p nil))
        (kill-buffer b)))))
(defun eam--create (&optional file)
  (make-directory eam-directory t)
  (let* ((path (or file (make-temp-file (expand-file-name "session-" eam-directory) nil ".txt")))
         (s (eam--session :file path :archived (and file t)
                              :input (generate-new-buffer "*AI input*")
                              :output (generate-new-buffer "*AI output*")))
         ready)
    (unwind-protect
        (progn
          (with-current-buffer (eam-session-output s)
            (eam-output-mode)
            (setq eam--current s)
            (add-hook 'kill-buffer-hook #'eam--detach nil t)
            (eam-history-latest))
          (with-current-buffer (eam-session-input s)
            (text-mode)
            (buffer-enable-undo)
            (setq eam--current s)
            (add-hook 'kill-buffer-hook #'eam--detach nil t))
          (push s eam--sessions)
          (setq ready t)
          s)
      (unless ready
        (setq eam--sessions (delq s eam--sessions))
        (dolist (buffer (list (eam-session-input s) (eam-session-output s)))
          (when (buffer-live-p buffer)
            (with-current-buffer buffer
              (let ((kill-buffer-query-functions nil))
                (set-buffer-modified-p nil)
                (kill-buffer buffer)))))))))
(defun eam-history-open (&optional file)
  "Choose a native conversation; with prefix, choose a raw diagnostic FILE."
  (interactive (list (and current-prefix-arg
                          (read-file-name "Raw diagnostic archive: "))))
  (if file
      (pop-to-buffer (eam-session-output (eam--create (expand-file-name file))))
    (require 'eam-native-history)
    (eam-native-history-open)))

;;;###autoload
(dolist (command '(eam-history-search eam-history-next eam-history-toggle-colors
                    eam-history-latest
                    eam-history-next-page
                    eam-history-previous-page
                    eam-output-mode))
  (put command 'completion-predicate #'ignore))

(provide 'eam)
;;; eam.el ends here
