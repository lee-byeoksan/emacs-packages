;;; eam-review.el --- Selected diff comments to an editable draft -*- lexical-binding: t; -*-
(require 'eam-terminal)
(require 'diff-mode)

(defcustom eam-review-selection-limit 12000
  "Maximum selected diff characters accepted for one review note.
Reduce the selected region when this limit is exceeded; nothing is truncated."
  :type 'natnum :group 'eam)

(defun eam-review--path (header)
  "Decode a unified diff HEADER path, including Git's quoted UTF-8 paths."
  (let ((path (car (split-string header "\t"))))
    (when (string-prefix-p "\"" path)
      (setq path (car (read-from-string path)))
      (when (cl-every (lambda (c) (< c 256)) path)
        (setq path (decode-coding-string (string-as-unibyte path) 'utf-8-unix))))
    (if (string-match "\\`[ab]/" path) (substring path 2) path)))

(defun eam-review--magit-paths ()
  "Read original old/new paths from Magit's retained diff header."
  (let ((header (and (fboundp 'magit-current-section)
                     (fboundp 'magit-diff-file-header)
                     (magit-diff-file-header (magit-current-section)))))
    (unless (and header (string-match "^--- \\(.*\\)$" header))
      (user-error "Cannot identify Magit's original diff header"))
    (let ((old (eam-review--path (match-string 1 header))))
      (unless (string-match "^+++ \\(.*\\)$" header)
        (user-error "Cannot identify Magit's new file header"))
      (list old (eam-review--path (match-string 1 header))))))

(defun eam-review--locations (beg end)
  "Collect file and old/new line spans intersecting BEG..END in unified diff.
Deletion-only lines have no new line number.  No source file is opened."
  (save-excursion
    (goto-char (point-min))
    (let (old-file new-file old new old-left new-left current spans)
      (while (< (point) end)
        (let ((line-start (point)) (line-end (line-end-position)))
          (cond
           ((and (or (not old-left) (and (= old-left 0) (= new-left 0)))
                 (looking-at "--- \\(.*\\)$"))
            (setq old-file (eam-review--path (match-string-no-properties 1))
                  current nil old-left nil new-left nil))
           ((and (not old-left) (looking-at "+++ \\(.*\\)$"))
            (setq new-file (eam-review--path (match-string-no-properties 1))))
           ((looking-at "@@ -\\([0-9]+\\)\\(?:,\\([0-9]+\\)\\)? +\\+\\([0-9]+\\)\\(?:,\\([0-9]+\\)\\)? @@")
            (setq old (string-to-number (match-string 1))
                  old-left (if (match-string 2) (string-to-number (match-string 2)) 1)
                  new (string-to-number (match-string 3))
                  new-left (if (match-string 4) (string-to-number (match-string 4)) 1))
            (when (derived-mode-p 'magit-mode)
              (let ((paths (eam-review--magit-paths)))
                (setq old-file (car paths) new-file (cadr paths))))
            (setq current (list :old-file old-file :new-file new-file
                                :old-start nil :old-end nil :new-start nil :new-end nil)))
           ((and current old-left (> (+ old-left new-left) 0)
                 (memq (char-after) '(?\s ?+ ?-)))
            (let* ((kind (char-after))
                   (has-old (memq kind '(?\s ?-)))
                   (has-new (memq kind '(?\s ?+))))
              (when (and (< line-start end) (> (min (point-max) (1+ line-end)) beg))
                (unless (and old-file new-file) (user-error "Cannot identify selected diff's file"))
                (unless (memq current spans) (push current spans))
                (when has-old
                  (unless (plist-get current :old-start) (setf (plist-get current :old-start) old))
                  (setf (plist-get current :old-end) old))
                (when has-new
                  (unless (plist-get current :new-start) (setf (plist-get current :new-start) new))
                  (setf (plist-get current :new-end) new)))
              (when has-old (cl-incf old) (cl-decf old-left))
              (when has-new (cl-incf new) (cl-decf new-left))))
           ((looking-at "diff ") (setq current nil old-left nil new-left nil old-file nil new-file nil))))
        (forward-line 1))
      (unless spans (user-error "Select lines inside a unified diff hunk"))
      (nreverse spans))))

(defun eam-review--range (start end)
  (cond ((null start) "none") ((= start end) (number-to-string start))
        (t (format "%d-%d" start end))))

(defun eam-review--capture (beg end)
  "Capture exactly the selected diff and its locations, with a hard size limit."
  (when (> (- end beg) eam-review-selection-limit)
    (user-error "Selected %d chars; limit %d. Reduce the region and retry"
                (- end beg) eam-review-selection-limit))
  (let ((position beg))
    (while (< position end)
      (when (invisible-p position)
        (user-error "Selection includes hidden diff text; expand it and select again"))
      (setq position (next-char-property-change position end))))
  (list :text (buffer-substring-no-properties beg end)
        :locations (eam-review--locations beg end)
        :directory default-directory))

(defun eam-review--render (capture comment)
  (let* ((text (plist-get capture :text)) (longest 2) (start 0))
    (while (string-match "`+" text start)
      (setq longest (max longest (- (match-end 0) (match-beginning 0)))
            start (match-end 0)))
    (let ((fence (make-string (1+ longest) ?`)))
      (concat "Review comment\nSource directory: " (plist-get capture :directory) "\n"
              (mapconcat
               (lambda (span)
                 (format "Old: %s:%s | New: %s:%s"
                         (plist-get span :old-file)
                         (eam-review--range (plist-get span :old-start) (plist-get span :old-end))
                         (plist-get span :new-file)
                         (eam-review--range (plist-get span :new-start) (plist-get span :new-end))))
               (plist-get capture :locations) "\n")
              "\nComment: " comment "\nSelected diff:\n" fence "diff\n" text
              (unless (string-suffix-p "\n" text) "\n") fence "\n"))))

;;;###autoload
(defun eam-review-selection (beg end)
  "Add selected diff lines and a comment to a chosen CLI's editable draft.
Does not send input or start a CLI.  Review the draft and transfer explicitly."
  (interactive "r")
  (unless (and (derived-mode-p 'diff-mode 'magit-mode) (use-region-p))
    (user-error "Select a diff region in diff-mode or Magit first"))
  (let* ((capture (eam-review--capture beg end))
         (comment (read-string (format "Review comment (%d selected chars): " (- end beg))))
         (sessions (cl-loop for s in eam-terminal--sessions
                            for buffer = (eam-terminal-output s)
                            when (buffer-live-p buffer) collect (cons (buffer-name buffer) s)))
         (_ (unless sessions (user-error "Start a CLI session first")))
         (session (cdr (assoc (completing-read "Draft session: " sessions nil t) sessions)))
         (text (eam-review--render capture comment)))
    (unless (and session (buffer-live-p (eam-terminal-output session)))
      (user-error "Selected session has closed"))
    (pop-to-buffer (eam-terminal--ensure-draft session))
    (goto-char (point-max))
    (undo-boundary)
    (atomic-change-group (unless (bolp) (insert "\n")) (insert text))
    (undo-boundary)
    (message "Draft only: %d added chars; inspect before C-c a p, then Enter in the CLI" (length text))))

(declare-function magit-current-section "magit-section" ())
(declare-function magit-diff-file-header "magit-diff" (section &optional no-rename))
(provide 'eam-review)
;;; eam-review.el ends here
