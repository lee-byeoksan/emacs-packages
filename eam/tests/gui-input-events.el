;;; gui-input-events.el --- Raw GUI input probe without package keymaps -*- lexical-binding: t; -*-
;; Load only in an explicitly isolated -Q GUI. read-event does not dispatch
;; received input to commands, so malformed input cannot invoke shell-command.
(require 'json)
(defun eam-probe-input-events ()
  "Capture at most 128 raw events for 180 seconds in this test GUI only."
  (interactive)
  (let* ((path (getenv "EMACS_AI_EVENT_REPORT"))
         (deadline (+ (float-time) 180))
         (count 0)
         (buffer (get-buffer-create "*Raw input probe*")))
    (unless (and path (file-name-absolute-p path) (not (file-exists-p path)))
      (error "A new absolute report path is required"))
    (switch-to-buffer buffer)
    (delete-other-windows)
    (setq buffer-read-only t)
    (let ((inhibit-read-only t))
      (insert "Raw input probe — Emacs -Q, no eam package\n"
              "Input is logged, never dispatched to commands.\n\n"))
    (unwind-protect
        (while (and (< count 128) (< (float-time) deadline))
          (redisplay t)
          (let ((event (read-event nil nil 1)))
            (when event
              (let* ((basic (event-basic-type event))
                     (record `((index . ,count)
                               (event . ,(prin1-to-string event))
                               (basic . ,(prin1-to-string basic))
                               (modifiers . ,(vconcat (mapcar #'symbol-name (event-modifiers event))))
                               (description . ,(single-key-description event))))
                     (line (concat (json-encode record) "\n")))
                (write-region line nil path t 'silent)
                (let ((inhibit-read-only t)) (insert line))
                (setq count (1+ count))))))
      (let ((inhibit-read-only t)) (insert "\nCapture ended.\n")))))
