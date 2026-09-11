;;; eam-demo.el --- Explicit offline test fixture -*- lexical-binding: t; -*-
(require 'eam)
(defcustom eam-response-batches 100 "Batches generated per submission." :type 'integer)

(defvar eam-input-mode-map
  (let ((map (make-sparse-keymap)))
    (define-key map (kbd "C-c C-c") #'eam-demo-send)
    (define-key map (kbd "C-c C-k") #'eam-demo-stop)
    map))
(define-derived-mode eam-input-mode text-mode "AI-input"
  "Editable input; C-c C-c submits to this session’s provider."
  (buffer-enable-undo))


(defun eam--batch (session)
  (let ((n (cl-incf (eam-session-sequence session))))
    (concat (format "\n[가짜 응답 %d] 한글 조합과 스트리밍 출력 검증 🧪\n```elisp\n(message \"안녕하세요 %d\")\n```\n" n n)
            (mapconcat (lambda (i)
                         (format "LOG %06d.%02d %s" n i
                                 (make-string 240 ?가)))
                       (number-sequence 1 8) "\n") "\n")))
(defun eam--tick (session)
  (condition-case err
      (when (> (eam-session-remaining session) 0)
        (eam--append session (eam--batch session))
        (cl-decf (eam-session-remaining session))
        (when (= (eam-session-remaining session) 0)
          (eam--cancel session)))
    (error (setf (eam-session-error session) (error-message-string err))
           (eam--cancel session)
           (message "AI prototype stopped: %s" (error-message-string err)))))

(defun eam-demo-stop ()
  (interactive) (eam--cancel eam--current))
(defun eam-demo-send ()
  (interactive)
  (if (eam-session-provider eam--current)
      (progn (require 'eam-connect) (eam-connect-send))
  (let ((s eam--current)
        (text (buffer-substring-no-properties (point-min) (point-max))))
    (when (eam-session-archived s) (user-error "Archive viewer; create a new session to send"))
    (unless (buffer-live-p (eam-session-output s)) (user-error "Output closed; create a new session"))
    (when (> (eam-session-remaining s) 0) (user-error "Response active; stop it first"))
    (when (= (length text) 0) (user-error "Input is empty"))
    (eam--append s (concat "\n[사용자]\n" text "\n[가짜 AI]\n"))
    ;; Keep the draft and its undo history intact; the user edits it for the next turn.
    (setf (eam-session-error s) nil
          (eam-session-remaining s) (max 1 eam-response-batches)
          (eam-session-timer s)
          (run-at-time 0 eam-interval #'eam--tick s)))))
(defun eam-demo-input ()
  (interactive) (pop-to-buffer (eam-session-input eam--current)))

(defun eam-demo-new ()
  (interactive)
  (let ((s (eam--create)))
    (pop-to-buffer (eam-session-output s))
    (display-buffer (eam-session-input s) '(display-buffer-pop-up-window))
    (pop-to-buffer (eam-session-input s))))

(defun eam-demo-stress (count)
  "Start COUNT independent fake streams for interactive GUI testing."
  (interactive "nNumber of fake sessions: ")
  (unless (and (integerp count) (> count 0)) (user-error "Use a positive integer"))
  (let (first)
    (dotimes (_ count)
      (let ((s (eam--create)))
        (unless first (setq first s))
        (with-current-buffer (eam-session-input s)
          (insert "한글 입력·코드·긴 로그 부하 실험")
          (eam-demo-send))))
    (pop-to-buffer (eam-session-output first))
    (display-buffer (eam-session-input first) '(display-buffer-pop-up-window))
    (pop-to-buffer (eam-session-input first))))


(defun eam-demo--configure (session)
  (with-current-buffer (eam-session-input session)
    (eam-input-mode)
    (setq eam--current session)
    (add-hook 'kill-buffer-hook #'eam--detach nil t))
  (with-current-buffer (eam-session-output session)
    (use-local-map (copy-keymap eam-output-mode-map))
    (local-set-key (kbd "i") #'eam-demo-input)
    (local-set-key (kbd "s") #'eam-demo-stop))
  session)
(advice-add 'eam--create :filter-return #'eam-demo--configure)
(provide 'eam-demo)
