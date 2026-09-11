;;; eam-persistent-events.el --- Bounded disk event reader -*- lexical-binding: t; -*-
(require 'cl-lib)
(require 'json)
(require 'eam-notifications)
(cl-defstruct eai-event-reader file identity buffer (offset 0) (pending "") (sequence 0) failed)

(defun eai-event-reader--create (file buffer)
  "Create a passive reader of at most the newest 64 KiB of FILE.
Only complete recent records are loaded.  The full journal remains on disk."
  (when (file-remote-p file) (error "Event journal must be local"))
  (let* ((attrs (file-attributes file))
         (size (file-attribute-size attrs))
         (offset (max 0 (- size 65536))))
    (unless (and attrs (null (file-attribute-type attrs)))
      (error "Event journal must be a regular file"))
    (when (> offset 0)
      (with-temp-buffer
        (set-buffer-multibyte nil)
        (insert-file-contents-literally file nil offset (min size (+ offset 32768)))
        (unless (search-forward "\n" nil t) (error "Oversized event record"))
        (setq offset (+ offset (1- (point))))))
    (make-eai-event-reader :file file :buffer buffer :offset offset
                          :identity (list (file-attribute-inode-number attrs)
                                          (file-attribute-device-number attrs)))))

(defvar-local eai-event-reader-current nil)
(defun eai-event-reader-open (file buffer)
  "Reuse BUFFER's reader for FILE so reattachment does not replay events."
  (setq file (expand-file-name file))
  (with-current-buffer buffer
    (if (and eai-event-reader-current
             (equal file (eai-event-reader-file eai-event-reader-current)))
        eai-event-reader-current
      (setq eai-event-reader-current (eai-event-reader--create file buffer)))))

(defun eai-event-reader--read-sequence (reader)
  "Read a bounded receipt; absent receipts represent unread events."
  (let ((file (expand-file-name "notification-read.json"
                                (file-name-directory (eai-event-reader-file reader)))))
    (if (not (file-exists-p file)) 0
      (with-temp-buffer
        (insert-file-contents file nil 0 4097)
        (when (> (buffer-size) 4096) (error "Oversized notification receipt"))
        (let ((seq (alist-get 'seq (json-parse-buffer :object-type 'alist))))
          (unless (natnump seq) (error "Invalid notification receipt"))
          seq)))))

(defun eai-event-reader-poll (reader)
  "Read at most 64 KiB this call; no AI, timers or processes are started.
Keep a partial line below 32 KiB and reject replacement/truncation."
  (when (eai-event-reader-failed reader) (error "Event reader stopped after an error"))
  (condition-case err
      (let* ((file (eai-event-reader-file reader))
             (attrs (file-attributes file))
             (offset (eai-event-reader-offset reader))
             (size (file-attribute-size attrs))
             (read-seq (eai-event-reader--read-sequence reader)))
        (unless (and attrs (null (file-attribute-type attrs))
                     (equal (eai-event-reader-identity reader)
                            (list (file-attribute-inode-number attrs)
                                  (file-attribute-device-number attrs)))
                     (>= size offset))
          (error "Event journal was replaced or truncated"))
        (with-temp-buffer
          (set-buffer-multibyte nil)
          (insert (eai-event-reader-pending reader))
          (let ((end (min size (+ offset 65536))))
            (insert-file-contents-literally file nil offset end)
            (setf (eai-event-reader-offset reader) end))
          (goto-char (point-min))
          (let ((start (point)))
            (while (search-forward "\n" nil t)
              (when (> (- (point) start) 32768) (error "Oversized event record"))
              (let* ((text (decode-coding-string (buffer-substring start (1- (point))) 'utf-8))
                     (event (json-parse-string text :object-type 'alist))
                     (seq (alist-get 'seq event))
                     (title (alist-get 'title event)) (body (alist-get 'body event)))
                (unless (and (eq (alist-get 'version event) 1) (integerp seq) (> seq 0)
                             (stringp title) (stringp body)
                             (<= (+ (length title) (length body)) 8192))
                  (error "Invalid event record"))
                (when (> seq (eai-event-reader-sequence reader))
                  (eam-notifications--receive
                   (eai-event-reader-buffer reader) title body
                   (directory-file-name (file-name-directory file)) seq (<= seq read-seq))
                  (setf (eai-event-reader-sequence reader) seq)))
              (setq start (point)))
            (when (>= (- (point-max) start) 32768) (error "Oversized partial event record"))
            (setf (eai-event-reader-pending reader) (buffer-substring start (point-max))))))
    (error (setf (eai-event-reader-failed reader) t) (signal (car err) (cdr err)))))
(provide 'eam-persistent-events)
;;; eam-persistent-events.el ends here
