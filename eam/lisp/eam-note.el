;;; eam-note.el --- Private notes owned by a CLI conversation -*- lexical-binding: t; -*-
(require 'eam-persistent)
(defvar-local eam-note--directory nil)
(defvar-local eam-note--identity nil)
(defvar-local eam-note--revision nil)
(defun eam-note--save ()
  "Save the private conversation note, retaining edits on a conflict."
  (when (> (string-bytes (buffer-string)) 65536)
    (user-error "Session note is limited to 64 KiB"))
  (let ((info (eam-persistent--call "note-save"
                `((session . ,eam-note--directory)
                  (identity . ,eam-note--identity)
                  (revision . ,eam-note--revision)
                  (text . ,(buffer-substring-no-properties (point-min) (point-max)))))))
    (setq eam-note--identity (alist-get 'identity info)
          eam-note--revision (alist-get 'revision info)))
  (set-buffer-modified-p nil)
  (message "Conversation note saved (never sent to the CLI)")
  t)
(defvar eam-note-mode-map
  (let ((map (make-sparse-keymap)))
    (define-key map (kbd "C-x C-s") #'eam-note-save)
    (define-key map (kbd "C-c C-c") #'eam-note-save-and-close)
    (define-key map (kbd "C-c C-k") #'eam-note-close)
    map))
(define-derived-mode eam-note-mode text-mode "EAM Note"
  "Persistent conversation notes.  Saving never submits anything to an AI."
  (buffer-enable-undo)
  (setq-local header-line-format
              '(:eval (concat " NOTE | C-x C-s save · C-c C-c save/close · C-c C-k discard/close | "
                              (if eam-note--identity "saved with CLI conversation"
                                "waiting for conversation ID; retained locally"))))
  (setq-local buffer-offer-save t)
  (add-hook 'write-contents-functions #'eam-note--save nil t))
(defun eam-note-save () (interactive) (eam-note--save))
(defun eam-note-save-and-close ()
  (interactive) (eam-note--save) (quit-window))
(defun eam-note-close ()
  "Discard unsaved edits and close the note; keep the saved note."
  (interactive)
  (when (or (not (buffer-modified-p)) (yes-or-no-p "Discard unsaved note edits? "))
    (set-buffer-modified-p nil) (quit-window t)))
;;;###autoload
(defun eam-note ()
  "Edit a private note that survives CLI exit and follows the resumed conversation."
  (interactive)
  (let* ((directory (eam-session-context-directory))
         (info (eam-persistent--call "note-read" `((session . ,directory))))
         (identity (alist-get 'identity info))
         (existing (seq-find
                    (lambda (b)
                      (with-current-buffer b
                        (and (derived-mode-p 'eam-note-mode)
                             (or (and identity (equal eam-note--identity identity))
                                 (and (null eam-note--identity)
                                      (equal eam-note--directory directory))))))
                    (buffer-list)))
         (buffer (or existing (generate-new-buffer "*EAM note*"))))
    (unless existing
      (with-current-buffer buffer
        (eam-note-mode)
        (setq eam-note--directory directory
              eam-note--identity identity
              eam-note--revision (alist-get 'revision info))
        (insert (or (alist-get 'text info) ""))
        (set-buffer-modified-p nil)))
    (when existing
      (with-current-buffer buffer
        (if (buffer-modified-p)
            (when (and (null eam-note--identity)
                       (equal eam-note--revision (alist-get 'revision info)))
              (setq eam-note--identity identity))
          (erase-buffer)
          (insert (or (alist-get 'text info) ""))
          (setq eam-note--directory directory
                eam-note--identity identity
                eam-note--revision (alist-get 'revision info))
          (set-buffer-modified-p nil))))
    (pop-to-buffer buffer)))
(provide 'eam-note)
(dolist (command '(eam-note-mode eam-note-save eam-note-save-and-close eam-note-close))
  (put command 'completion-predicate #'ignore))
;;; eam-note.el ends here
