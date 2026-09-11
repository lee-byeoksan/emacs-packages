;;; pty-backend-test.el --- Default PTY sessions through public EAM -*- lexical-binding: t; -*-
(require 'ert)
(require 'eam-app)
(defconst eam-pty-backend-test-root
  (expand-file-name ".." (file-name-directory (or load-file-name buffer-file-name))))
(defun eam-pty-backend-test-wait (predicate)
  (let ((deadline (+ (float-time) 7)))
    (while (and (not (funcall predicate)) (< (float-time) deadline))
      (accept-process-output nil .025))
    (should (funcall predicate))))
(ert-deftest eam-pty-backend-default-new-detach-attach-quit ()
  (let* ((eam-directory (make-temp-file "eam-pty 한글 ' -" t))
         (eam-record-terminal nil)
         (eam-persistent--readers (make-hash-table :test #'equal))
         (eam-notifications--entries nil)
         (fixture (or (getenv "EAM_NATIVE_FIXTURE")
                      (expand-file-name "var/native-target/debug/examples/fixture" eam-pty-backend-test-root)))
         session path runtime draft)
    (unwind-protect
        (progn
          (cl-letf (((symbol-function 'eam--executable) (lambda (_name) fixture))
                    ((symbol-function 'eam-notifications-cli-args)
                     (lambda (_provider)
                       (list "terminal"
                             (expand-file-name "received.jsonl" eam-directory)))))
            (setq session (eam-new "Claude" eam-directory)))
          (setq path (buffer-local-value 'eam-terminal-persistent-directory (eam-terminal-output session)))
          (let* ((info (eam-persistent--call "inspect" `((session . ,path))))
                 (metadata (alist-get 'metadata info)))
            (setq runtime (alist-get 'runtime metadata))
            (should (equal (alist-get 'backend metadata) "pty"))
            (should-not (alist-get 'archive metadata))
            (should (= 1 (alist-get 'attached_clients (alist-get 'status info)))))
          (eam-pty-backend-test-wait
           (lambda () (with-current-buffer (eam-terminal-output session)
                        (string-match-p "READY" (buffer-string)))))
          (with-current-buffer (eam-terminal-output session)
            (should (memq #'eam-terminal--window-buffer-change window-buffer-change-functions))
            (should-not (memq #'ghostel--window-buffer-change window-buffer-change-functions))
            (should (memq #'eam-terminal--defer-synchronized-redraw
                          ghostel-inhibit-redraw-functions))
            (should-not (ghostel--mouse-tracking-p ghostel--term))
            ;; Use the real module as well as the isolated scheduling test.
            (let ((ghostel--force-next-redraw nil))
              (ghostel--write-vt ghostel--term "\e[?2026h")
              (should (eam-terminal--defer-synchronized-redraw (current-buffer)))
              (ghostel--write-vt ghostel--term "\e[?2026l")
              (should-not (eam-terminal--defer-synchronized-redraw (current-buffer))))
            (eam-detach))
          (eam-pty-backend-test-wait
           (lambda () (zerop (alist-get 'attached_clients
                                       (alist-get 'status (eam-persistent--call "inspect" `((session . ,path))))))))
          (should (= 1 (length (alist-get 'sessions (eam-persistent--call "list-live"
                                                    `((root . ,(expand-file-name "persistent" eam-directory))))))))
          (setq session (eam-attach path))
          (eam-pty-backend-test-wait
           (lambda () (with-current-buffer (eam-terminal-output session)
                        (string-match-p "READY" (buffer-string)))))
          (with-current-buffer (eam-terminal-output session)
            (setq draft (eam-terminal--ensure-draft session))
            (with-current-buffer draft (insert "한글 PTY 입력"))
            (eam-paste))
          (eam-pty-backend-test-wait
           (lambda () (with-current-buffer (eam-terminal-output session)
                        (string-match-p "PASTED 한글 PTY 입력" (buffer-string)))))
          (with-current-buffer (eam-terminal-output session) (eam-edit-input))
          (eam-pty-backend-test-wait
           (lambda () (seq-find (lambda (b) (with-current-buffer b
                                             (and buffer-file-name (string-suffix-p ".editor.txt" buffer-file-name))))
                                (buffer-list))))
          (let ((editor (seq-find (lambda (b) (with-current-buffer b
                                               (and buffer-file-name (string-suffix-p ".editor.txt" buffer-file-name))))
                                 (buffer-list))))
            (with-current-buffer editor
              (goto-char (point-max)) (insert " 수정")
              (call-interactively (key-binding (kbd "C-c C-c")))))
          (eam-pty-backend-test-wait
           (lambda () (with-current-buffer (eam-terminal-output session)
                        (string-match-p "EDITED 한글 PTY 입력 수정" (buffer-string)))))
          (cl-letf (((symbol-function 'yes-or-no-p) (lambda (&rest _) t)))
            (eam-quit path))
          (should (equal "stopped" (alist-get 'state (alist-get 'status
                                      (eam-persistent--call "inspect" `((session . ,path))))))))
      (when path (ignore-errors (eam-persistent--call "stop" `((session . ,path)))))
      (when (and session (buffer-live-p (eam-terminal-output session)))
        (with-current-buffer (eam-terminal-output session) (eam-detach)))
      (when (buffer-live-p draft) (with-current-buffer draft (set-buffer-modified-p nil)) (kill-buffer draft))
      (when runtime (delete-directory runtime t))
      (delete-directory eam-directory t))))

(ert-deftest eam-pty-backend-detached-natural-exit ()
  (let* ((root (make-temp-file "eam-pty-exit-" t))
         (path (expand-file-name "session" root)) metadata runtime)
    (unwind-protect
        (progn
          (setq metadata (eam-persistent--call "start"
                          `((session . ,path) (provider . "Fake") (backend . "pty")
                            (directory . ,root) (executable . ,(or (getenv "EAM_NATIVE_FIXTURE")
                                              (expand-file-name "var/native-target/debug/examples/fixture" eam-pty-backend-test-root)))
                            (raw_recording . t)
                            (args . ["exit"]))))
          (setq runtime (alist-get 'runtime metadata))
          (eam-pty-backend-test-wait
           (lambda () (equal "stopped" (alist-get 'state (alist-get 'status
                                          (eam-persistent--call "inspect" `((session . ,path))))))))
          (should-not (file-exists-p (expand-file-name "socket" runtime)))
          (should (equal 0 (alist-get 'exit_code (alist-get 'status
                              (eam-persistent--call "inspect" `((session . ,path)))))))
          (with-temp-buffer
            (insert-file-contents (alist-get 'archive metadata))
            (should (string-match-p "한글 종료" (buffer-string)))))
      (when metadata (ignore-errors (eam-persistent--call "stop" `((session . ,path)))))
      (when runtime (delete-directory runtime t))
      (delete-directory root t))))

(ert-deftest eam-pty-backend-idle-window-width-propagates ()
  "Resize an idle Ghostel window through attach, daemon and recorder PTYs."
  (save-window-excursion
    (let* ((root (make-temp-file "eam-pty-size-" t))
           (eam-directory root)
           (path (expand-file-name "session" root))
           (sizes (expand-file-name "width" root))
           metadata runtime session window)
      (unwind-protect
          (progn
            (setq metadata
                  (eam-persistent--call "start"
                   `((session . ,path) (provider . "Fake") (backend . "pty")
                     (directory . ,root) (executable . ,(or (getenv "EAM_NATIVE_FIXTURE")
                                              (expand-file-name "var/native-target/debug/examples/fixture" eam-pty-backend-test-root)))
                     (args . ["size"]))))
            (setq runtime (alist-get 'runtime metadata)
                  session (eam-attach path)
                  window (get-buffer-window (eam-terminal-output session)))
            (select-window window)
            (delete-other-windows)
            (dolist (split '(t nil))
              (if split
                  (set-window-buffer (split-window-right) (get-buffer-create "*scratch*"))
                (delete-other-windows))
              (with-current-buffer (eam-terminal-output session)
                (ghostel--adjust-size window t)
                (let ((expected ghostel--term-cols))
                  (eam-pty-backend-test-wait
                   (lambda ()
                     (and (file-exists-p sizes)
                          (= expected (with-temp-buffer
                                        (insert-file-contents sizes)
                                        (string-to-number (buffer-string)))))))))))
        (when metadata (ignore-errors (eam-persistent--call "stop" `((session . ,path)))))
        (when (and session (buffer-live-p (eam-terminal-output session)))
          (with-current-buffer (eam-terminal-output session) (eam-detach)))
        (when runtime (delete-directory runtime t))
        (delete-directory root t)))))

(ert-deftest eam-pty-backend-cli-quit-cleans-attached-buffer ()
  "CLI quit closes its display, even when a helper retains the slave PTY."
  (dolist (mode '("echo" "inherited-pty"))
    (let* ((root (make-temp-file "eam-pty-cli-quit-" t))
           (eam-directory root)
           (path (expand-file-name "session" root))
           (eam-persistent--readers (make-hash-table :test #'equal))
           session runtime draft)
      (unwind-protect
          (cl-letf (((symbol-function 'yes-or-no-p)
                     (lambda (&rest _) (ert-fail "Automatic CLI exit prompted"))))
            (let ((v (eam-persistent--call "start"
                      `((session . ,path) (provider . "Fake") (backend . "pty")
                        (directory . ,root) (executable . ,(getenv "EAM_NATIVE_FIXTURE"))
                        (args . [,mode])))))
              (setq runtime (alist-get 'runtime v)))
            (setq session (eam-attach path))
            (with-current-buffer (eam-terminal-output session)
              (setq draft (eam-terminal--ensure-draft session)))
            (with-current-buffer draft (insert "보존할 미전송 입력"))
            (process-send-string (eam-terminal-process session) "q")
            (eam-pty-backend-test-wait
             (lambda () (not (buffer-live-p (eam-terminal-output session)))))
            (should (equal "stopped" (alist-get 'state (alist-get 'status
                                     (eam-persistent--call "inspect" `((session . ,path)))))))
            (should-not (alist-get 'sessions (eam-persistent--call "list-live" `((root . ,root)))))
            (should (buffer-live-p draft))
            (with-current-buffer draft
              (should (equal "보존할 미전송 입력" (buffer-string)))))
        (ignore-errors (eam-persistent--call "stop" `((session . ,path))))
        (when (and session (buffer-live-p (eam-terminal-output session)))
          (with-current-buffer (eam-terminal-output session) (eam-detach)))
        (when (buffer-live-p draft)
          (with-current-buffer draft (set-buffer-modified-p nil)) (kill-buffer draft))
        (when runtime (delete-directory runtime t))
        ;; A failed setup may not own a server; never unlink another Emacs socket.
        (when (process-live-p server-process) (server-force-delete))
        (delete-directory root t)))))

(ert-deftest eam-pty-backend-cli-inherits-renderer-capabilities ()
  "The CLI behind the daemon must see the same terminal as Ghostel."
  (let* ((eam-directory (make-temp-file "eam-terminal-env-" t))
         (output (expand-file-name "env.json" eam-directory))
         (expected (ghostel--terminal-env))
         path runtime)
    (unwind-protect
        (cl-letf (((symbol-function 'eam--executable)
                   (lambda (_) (getenv "EAM_NATIVE_FIXTURE")))
                  ((symbol-function 'eam-notifications-cli-args) (lambda (_) nil))
                  ((symbol-function 'eam-attach)
                   (lambda (dir) (setq path dir))))
          (eam-persistent--start "Claude" eam-directory
                                 (list "terminal-env" output))
          (eam-pty-backend-test-wait (lambda () (file-exists-p output)))
          (setq runtime (alist-get 'runtime
                          (alist-get 'metadata
                            (eam-persistent--call "inspect" `((session . ,path))))))
          (let ((actual (with-temp-buffer
                          (insert-file-contents output)
                          (json-parse-buffer :object-type 'alist))))
            (dolist (entry expected)
              (when (string-match "\\`\\(TERM\\|TERMINFO\\|TERM_PROGRAM\\|COLORTERM\\)=\\(.*\\)\\'" entry)
                (should (equal (alist-get (intern (match-string 1 entry)) actual)
                               (match-string 2 entry)))))))
      (when path (ignore-errors (eam-persistent--call "stop" `((session . ,path)))))
      (when runtime (ignore-errors (delete-directory runtime t)))
      (delete-directory eam-directory t))))

(ert-deftest eam-pty-synchronized-redraw-skips-window-work ()
  (with-temp-buffer
    (let ((ghostel--term 'fake)
          (ghostel--force-next-redraw nil)
          (ghostel--redraw-timer nil)
          (ghostel-inhibit-redraw-functions
           '(eam-terminal--defer-synchronized-redraw))
          (synchronized t)
          (window-lookups 0))
      (cl-letf (((symbol-function 'ghostel--terminal-live-p) (lambda () t))
                ((symbol-function 'ghostel--mode-enabled)
                 (lambda (_term mode) (should (= mode 2026)) synchronized))
                ((symbol-function 'ghostel--get-render-window)
                 (lambda (_buffer) (cl-incf window-lookups) nil)))
        (unwind-protect
            (progn
              (dotimes (_ 100) (ghostel--redraw-now (current-buffer)))
              (should (= window-lookups 0))
              (should ghostel--pending-redraw)
              (should (timerp ghostel--redraw-timer))
              (ghostel--redraw-now (current-buffer) t)
              (should (= window-lookups 1))
              (setq ghostel--force-next-redraw nil synchronized nil)
              (ghostel--redraw-now (current-buffer))
              (should (= window-lookups 2)))
          (when ghostel--redraw-timer (cancel-timer ghostel--redraw-timer)))))))

(ert-deftest eam-pty-backend-reconnect-reuses-buffer-and-draft ()
  "Remote return keeps the same terminal buffer, draft and CLI process."
  (let* ((eam-directory (make-temp-file "eam-reuse-" t))
         (eam-record-terminal nil)
         (eam-persistent--readers (make-hash-table :test #'equal))
         (fixture (or (getenv "EAM_NATIVE_FIXTURE")
                      (expand-file-name "var/native-target/debug/examples/fixture" eam-pty-backend-test-root)))
         session path runtime output draft original-pid)
    (unwind-protect
        (progn
          (cl-letf (((symbol-function 'eam--executable) (lambda (_) fixture))
                    ((symbol-function 'eam-notifications-cli-args)
                     (lambda (_) (list "terminal" (expand-file-name "received.jsonl" eam-directory)))))
            (setq session (eam-new "Claude" eam-directory)))
          (setq output (eam-terminal-output session)
                path (buffer-local-value 'eam-terminal-persistent-directory output)
                draft (eam-terminal--ensure-draft session))
          (with-current-buffer draft (insert "보존할 미전송 초안"))
          (let ((info (eam-persistent--call "inspect" `((session . ,path)))))
            (setq runtime (alist-get 'runtime (alist-get 'metadata info))
                  original-pid (alist-get 'recorder_pid (alist-get 'status info))))
          (delete-process (eam-terminal-process session))
          (eam-pty-backend-test-wait
           (lambda () (zerop (alist-get 'attached_clients
                              (alist-get 'status (eam-persistent--call "inspect" `((session . ,path))))))))
          (should (eq session (eam-attach path)))
          (should (eq output (eam-terminal-output session)))
          (should (eq draft (eam-terminal-input session)))
          (should (equal (with-current-buffer draft (buffer-string)) "보존할 미전송 초안"))
          (should (with-current-buffer draft (buffer-modified-p)))
          (should (process-live-p (eam-terminal-process session)))
          (should (= original-pid (alist-get 'recorder_pid
                                    (alist-get 'status (eam-persistent--call "inspect" `((session . ,path)))))))
          (should (= 1 (cl-count session eam-terminal--sessions :test #'eq))))
      (when path (ignore-errors (eam-persistent--call "stop" `((session . ,path)))))
      (when (buffer-live-p output) (with-current-buffer output (eam-detach)))
      (when runtime (delete-directory runtime t))
      (delete-directory eam-directory t))))
