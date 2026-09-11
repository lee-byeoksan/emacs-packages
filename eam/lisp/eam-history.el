;;; eam-history.el --- Bounded asynchronous archive search -*- lexical-binding: t; -*-
(require 'eam)
(require 'eam-native)
(require 'json)
(defvar-local eam-history--process nil)
(defvar-local eam-history--query nil)
(defvar-local eam-history--offset nil)

(defun eam-history-cancel ()
  "Cancel this viewer's search, leaving the archive and CLI untouched."
  (interactive)
  (let ((process eam-history--process))
    (setq eam-history--process nil)
    (when (process-live-p process) (delete-process process))))

;;;###autoload
(defun eam-history-search (query &optional next)
  "Search literal UTF-8 QUERY asynchronously; NEXT continues after the last hit."
  (interactive (list (read-string "Search archive (literal): " eam-history--query)))
  (unless (derived-mode-p 'eam-output-mode) (user-error "Open a record viewer first"))
  (let* ((needle (encode-coding-string query 'utf-8-unix))
         (bound (min 4096 (- (eam--limit) 8)))
         (start (if (and next eam-history--offset) (1+ eam-history--offset) 0))
         (session eam--current)
         (buffer (current-buffer))
         (native (eam-native--executable)))
    (unless (<= 1 (length needle) bound)
      (user-error "Search must be 1..%d UTF-8 bytes with this page limit" bound))
    (eam-history-cancel)
    (unless next (setq eam-history--offset nil))
    (setq eam-history--query query)
    (add-hook 'kill-buffer-hook #'eam-history-cancel nil t)
    (setq eam-history--process
          (make-process
           :name "eam-archive-search" :buffer nil :noquery t :connection-type 'pipe
           :coding 'binary :command (list native "search-archive" (eam-session-file session)
                                         (number-to-string start))
           :filter (lambda (process text)
                     (let ((result (concat (process-get process 'result) text)))
                       (if (> (length result) 8192) (delete-process process)
                         (process-put process 'result result))))
           :sentinel
           (lambda (process _event)
             (when (and (buffer-live-p buffer) (memq (process-status process) '(exit signal)))
               (with-current-buffer buffer
                 (when (eq process eam-history--process)
                   (setq eam-history--process nil)
                   (condition-case err
                       (let* ((result (json-parse-string (or (process-get process 'result) "")
                                                         :object-type 'alist :null-object nil))
                              (offset (alist-get 'offset result))
                              (size (alist-get 'size result)))
                         (when (alist-get 'error result) (error "%s" (alist-get 'error result)))
                         (if (null offset) (message "No further match in archive snapshot")
                           (setq eam-history--offset offset)
                           (let* ((context (min 256 (/ (- (eam--limit) 4 (length needle)) 2)))
                                  (a (max 0 (- offset context)))
                                  (b (min size (+ a (- (eam--limit) 4)))))
                             (setf (eam-session-cursor session) (cons a b))
                             (eam--page session a b)
                             (search-forward query nil t)
                             (message "Archive match at byte %d; M-x eam-history-next for next" offset))))
                     (error (message "Archive search failed: %s" (error-message-string err))))))))))
    (condition-case err
        (progn
          (process-send-string eam-history--process needle)
          (process-send-eof eam-history--process))
      ((error quit)
       (eam-history-cancel)
       (signal (car err) (cdr err))))
    (message "Searching archive; M-x eam-history-cancel to stop")))

(defun eam-history-next ()
  (interactive)
  (unless eam-history--query (user-error "Search for a string first"))
  (eam-history-search eam-history--query t))
(defun eam-history-toggle-colors ()
  "Toggle rendered ANSI colors versus raw control codes on this page."
  (interactive)
  (setq eam-history-hide-colors (not eam-history-hide-colors))
  (let ((cursor (eam-session-cursor eam--current)))
    (if cursor (eam--page eam--current (car cursor) (cdr cursor))
      (eam-history-latest)))
  (message "ANSI color rendering %s; cursor/erase controls are not replayed"
           (if eam-history-hide-colors "on" "off")))
;;;###autoload
(dolist (command '(eam-history-cancel
                    eam-history-next
                    eam-history-search
                    eam-history-toggle-colors))
  (put command 'completion-predicate #'ignore))

(provide 'eam-history)
