;;; gui-driver.el --- Menus for manual/Computer Use checks -*- lexical-binding: t; -*-
(load (expand-file-name "support/eam-demo.el" (file-name-directory (or load-file-name buffer-file-name))) nil t)
;; Load only in the isolated GUI test app, never in personal init files.
(require 'eam)
(require 'easymenu)
(require 'json)
(defvar eam-gui-pair nil)
(defvar eam-gui-load 0)
(defvar eam-gui-paste-diagnostic nil)
(defvar-local eam-gui-events nil)
(defvar-local eam-gui-trace-enabled nil)
(defun eam-gui-event (&optional native)
  "Bounded instrumentation only in the two explicit comparison buffers."
  (when eam-gui-trace-enabled
    (push (list :command (format "%s" this-command)
                :event (format "%s" last-input-event)
                :native native
                :working (and (boundp 'ns-working-text) (stringp ns-working-text)
                              (substring ns-working-text 0 (min 80 (length ns-working-text))))
                :text (buffer-substring-no-properties
                       (max (point-min) (- (point-max) 80)) (point-max)))
          eam-gui-events)
    (when (> (length eam-gui-events) 100)
      (setcdr (nthcdr 99 eam-gui-events) nil))))
(defun eam-gui-native-event (&rest _)
  (eam-gui-event t))
(when (fboundp 'ns-insert-working-text)
  (advice-add 'ns-insert-working-text :after #'eam-gui-native-event))

(defun eam-gui-compare (count)
  "Create a fresh text-mode/prototype pair with COUNT active fake streams."
  (interactive "nBackground streams: ")
  (eam-gui-stop-all)
  (setq eam-gui-load count eam-gui-paste-diagnostic nil)
  (let* ((plain (generate-new-buffer "*IME A plain*"))
         (s (eam--create))
         (input (eam-session-input s)))
    (setq eam-gui-pair (list plain input))
    (with-current-buffer plain
      (text-mode)
      (setq-local header-line-format "A: plain text-mode — native keyboard comparison"))
    (with-current-buffer input
      (setq-local header-line-format "B: prototype input — same keys as A"))
    (dolist (buffer eam-gui-pair)
      (with-current-buffer buffer
        (setq eam-gui-trace-enabled t)
        (add-hook 'post-command-hook #'eam-gui-event nil t)))
    (dotimes (i count)
      (let ((stream (if (= i 0) s (eam--create))))
        (with-current-buffer (eam-session-input stream)
          (insert "background fixture")
          (let ((eam-response-batches 2400)) (eam-demo-send))
          ;; Reset only the new synthetic draft, before the input comparison.
          (erase-buffer)
          (setq buffer-undo-list nil))))
    (delete-other-windows)
    (switch-to-buffer plain)
    (let* ((a (selected-window))
           (b (split-window a nil 'below))
           (out (split-window b nil 'below)))
      (set-window-buffer b input)
      (set-window-buffer out (eam-session-output s))
      (select-window a))))
(defun eam-gui-ready ()
  "Start the keyboard handoff with no streams and no diagnostic bindings."
  (interactive)
  (eam-gui-compare 0))
(defun eam-gui-capture ()
  "Append an immutable snapshot; never inspect unrelated user buffers."
  (interactive)
  (let ((snapshot
         `((time . ,(current-time-string)) (background_streams . ,eam-gui-load)
           (paste_diagnostic . ,eam-gui-paste-diagnostic)
           (native_trace_installed . ,(and (fboundp 'ns-insert-working-text)
                                          (advice-member-p #'eam-gui-native-event
                                                           'ns-insert-working-text) t))
           (buffers . ,(vconcat
                        (mapcar
                         (lambda (b)
                           (with-current-buffer b
                             `((name . ,(buffer-name)) (mode . ,(symbol-name major-mode))
                               (text . ,(buffer-string))
                               (codepoints . ,(vconcat (string-to-list (buffer-string))))
                               (emacs_input_method . ,current-input-method)
                               (events . ,(vconcat (reverse eam-gui-events))))))
                         eam-gui-pair))))))
    (let ((coding-system-for-write 'utf-8-unix))
      (write-region (concat (json-encode snapshot) "\n") nil
                    (expand-file-name "ime-comparison.jsonl" eam-directory) t 'silent))
    (message "Comparison saved: %d streams" eam-gui-load)))

(defun eam-gui-paste-diagnostic ()
  "Map the observed synthetic Super+U+1169 event in comparison buffers only.
This is a diagnostic for Computer Use, not a production keyboard workaround."
  (interactive)
  (dolist (b eam-gui-pair)
    (with-current-buffer b
      (use-local-map (copy-keymap (current-local-map)))
      (define-key (current-local-map) (vector 8393065) #'clipboard-yank)))
  (setq eam-gui-paste-diagnostic t)
  (message "Diagnostic paste mapping enabled only for this test pair"))

;; Available in plain text-mode as well, within this isolated test app only.
(easy-menu-define eam-comparison-menu global-map "Input comparison controls."
  '("IME Check"
    ["New pair: idle" (eam-gui-compare 0) t]
    ["New pair: 1 stream" (eam-gui-compare 1) t]
    ["New pair: 12 streams" (eam-gui-compare 12) t]
    ["Capture both inputs" eam-gui-capture eam-gui-pair]
    ["Diagnostic paste binding (this pair only)" eam-gui-paste-diagnostic eam-gui-pair]
    ["Stop background streams" eam-gui-stop-all t]))
(defun eam-gui-report ()
  (interactive)
  (let ((report
         (list :time (current-time-string) :pid (emacs-pid)
               :graphical (display-graphic-p) :window-system window-system
               :sessions
               (mapcar
                (lambda (s)
                  (list :file (eam-session-file s)
                        :remaining (eam-session-remaining s)
                        :error (eam-session-error s)
                        :history (eam-session-cursor s)
                        :output-size (with-current-buffer (eam-session-output s) (buffer-size))
                        :output-undo-disabled (with-current-buffer (eam-session-output s) (eq buffer-undo-list t))
                        :input (with-current-buffer (eam-session-input s) (buffer-string))
                        :input-undo-enabled (with-current-buffer (eam-session-input s) (not (eq buffer-undo-list t)))))
                eam--sessions))))
    (with-temp-file (expand-file-name "gui-report.el" eam-directory)
      (prin1 report (current-buffer)))
    (message "GUI report saved (%d sessions)" (length eam--sessions))))
(defun eam-gui-sample ()
  (interactive)
  (insert "\n한글 입력 표본: 안녕하세요 값이 바뀝니다 🙂\n"))
(defun eam-gui-long-response ()
  (interactive)
  (setq eam-response-batches 1000)
  (eam-demo-send))
(defun eam-gui-stop-all ()
  (interactive)
  (mapc #'eam--cancel eam--sessions))
(dolist (map (list eam-input-mode-map eam-output-mode-map))
  (easy-menu-define eam-gui-menu map "GUI verification only."
    '("AI Test"
      ["Insert Korean sample (fixture)" eam-gui-sample (derived-mode-p 'eam-input-mode)]
      ["Undo input" undo (derived-mode-p 'eam-input-mode)]
      ["Stream for 50 seconds" eam-gui-long-response (derived-mode-p 'eam-input-mode)]
      ["Previous page" eam-history-previous-page (derived-mode-p 'eam-output-mode)]
      ["Next page" eam-history-next-page (derived-mode-p 'eam-output-mode)]
      ["Latest (live)" eam-history-latest (derived-mode-p 'eam-output-mode)]
      ["Start 12 additional streams" (eam-demo-stress 12) t]
      ["Save report" eam-gui-report t]
      ["Stop all test streams" eam-gui-stop-all t])))
