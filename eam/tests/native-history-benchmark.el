;;; native-history-benchmark.el --- Offline bounded viewers -*- lexical-binding: t; -*-
(require 'eam-native-history)
(let* ((root (make-temp-file "eam-native-bench-" t))
       (file (expand-file-name "conversation.jsonl" root))
       (eam-buffer-limit 65536) buffers results
       (ticks 0) (last (float-time)) (gap 0)
       (timer (run-at-time 0 .01 (lambda ()
                                 (let ((now (float-time)))
                                   (setq gap (max gap (- now last)) last now))
                                 (cl-incf ticks)))))
  (unwind-protect
      (progn
        (with-temp-file file
          (insert (json-encode `((type . "assistant") (message . ((role . "assistant") (content . ,(make-string 2000000 ?한)))))) "\n"))
        (dolist (count '(1 4 12))
          (let ((started (float-time)))
            (while (< (length buffers) count)
              (let ((buffer (generate-new-buffer " *native-benchmark*")))
                (push buffer buffers)
                (with-current-buffer buffer
                  (eam-native-history-mode)
                  (setq eam-native-history--entry `((provider . "Claude") (id . "fake") (path . ,file)))
                  (eam-native-history-latest))))
            (push `((viewers . ,count) (seconds . ,(- (float-time) started))
                    (buffer_chars . ,(apply #'+ (mapcar (lambda (b) (with-current-buffer b (buffer-size))) buffers))))
                  results)))
        (princ (json-encode `((samples . ,(vconcat (nreverse results))) (timer_ticks . ,ticks)
                             (max_timer_gap_seconds . ,gap)))))
    (cancel-timer timer)
    (mapc #'kill-buffer buffers)
    (delete-directory root t)))
