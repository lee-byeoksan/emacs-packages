;;; ghostel-test.el --- PTY daemon and real Ghostel -*- lexical-binding: t; -*-
(require 'ert)
(load (expand-file-name "ghostel-probe.el"
                        (file-name-directory (or load-file-name buffer-file-name))))
(defun eam-pty-test-wait (predicate)
  (let ((deadline (+ (float-time) 6)))
    (while (and (not (funcall predicate)) (< (float-time) deadline))
      (accept-process-output nil .01))
    (should (funcall predicate))))
(ert-deftest eam-pty-probe-ghostel-detach-reattach ()
  (let (first second runtime)
    (unwind-protect
        (progn
          (setq first (eam-pty-probe-new) runtime eam-pty-probe-runtime)
          (eam-pty-test-wait
           (lambda () (with-current-buffer first
                        (string-match-p "READY:" (buffer-string)))))
          (with-current-buffer first
            ;; This transport does not force alternate screen or mouse mode.
            (should-not (ghostel-alt-screen-p))
            (should-not (ghostel--mouse-tracking-p ghostel--term))
            (should (string-match-p "ROW 0000" (buffer-string)))
            (should (eq buffer-undo-list t))
            (set-process-query-on-exit-flag (get-buffer-process first) nil))
          (kill-buffer first)
          (eam-pty-test-wait
           (lambda () (with-temp-buffer
                        (insert-file-contents (expand-file-name "state.json" runtime))
                        (string-match-p "detached" (buffer-string)))))
          (setq second (eam-pty-probe-attach runtime))
          (eam-pty-test-wait
           (lambda () (with-current-buffer second
                        (string-match-p "ROW 0499" (buffer-string)))))
          (with-current-buffer second
            (ghostel--send-string "한글검증"))
          (eam-pty-test-wait
           (lambda () (with-current-buffer second
                        (string-match-p "한글검증" (buffer-string)))))
          (with-current-buffer second (ghostel-send-key "q"))
          (eam-pty-test-wait
           (lambda () (not (file-exists-p (expand-file-name "socket" runtime))))))
      (dolist (buffer (list first second))
        (when (buffer-live-p buffer)
          (when-let ((process (get-buffer-process buffer)))
            (set-process-query-on-exit-flag process nil))
          (kill-buffer buffer)))
      ;; Successful fixture exits itself; preserve a failed runtime for diagnosis.
      (when (and runtime (not (file-exists-p (expand-file-name "socket" runtime))))
        (delete-directory runtime t)))))
