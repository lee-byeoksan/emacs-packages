;;; gui-scroll-fixture.el --- Finite fake GUI output fixture -*- lexical-binding: t; -*-
(load (expand-file-name "support/eam-demo.el" (file-name-directory (or load-file-name buffer-file-name))) nil t)
(require 'eam)
(require 'json)
(setq eam-directory (file-name-as-directory (getenv "EMACS_AI_SCROLL_ROOT"))
      eam-response-batches 2400)
(when (file-exists-p eam-directory) (error "Use a new fixture directory"))
(make-directory eam-directory t)
(eam-demo-stress 4)
(pop-to-buffer (eam-session-output eam--current))
(delete-other-windows)
(let ((root eam-directory) (samples 0) sampler)
  (setq sampler
        (run-at-time
         1 1
         (lambda ()
           (let ((record
                  `((sample . ,samples)
                    (window_start . ,(window-start))
                    (point . ,(point))
                    (sessions . ,(vconcat
                                  (mapcar
                                   (lambda (s)
                                     `((buffer_chars . ,(with-current-buffer (eam-session-output s) (buffer-size)))
                                       (undo_off . ,(with-current-buffer (eam-session-output s) (eq buffer-undo-list t)))
                                       (remaining . ,(eam-session-remaining s))
                                       (archive_bytes . ,(file-attribute-size (file-attributes (eam-session-file s))))))
                                   eam--sessions))))))
             (write-region (concat (json-encode record) "\n") nil
                           (expand-file-name "samples.jsonl" root) t 'silent))
           (setq samples (1+ samples))
           (when (>= samples 125)
             (mapc #'eam--cancel eam--sessions)
             (cancel-timer sampler))))))
