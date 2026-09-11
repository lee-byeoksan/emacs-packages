;;; cli-validation-gui.el --- GUI checks for recorded test conversations -*- lexical-binding: t; -*-
(load (expand-file-name "terminal-gui.el" (file-name-directory (or load-file-name buffer-file-name))) nil t)
(defun ai-validation-gui-resume (provider)
  (let* ((dir (expand-file-name (concat "var/cli-validation/20260908/" provider) eam-terminal-gui-root))
         (workspace (expand-file-name "workspace" dir))
         (sid (string-trim (with-temp-buffer (insert-file-contents (expand-file-name "session-id.txt" dir)) (buffer-string))))
         (args (if (equal provider "claude") (list "--resume" sid "--permission-mode" "manual")
                 (list "resume" sid "-c" (concat "projects={" (json-encode-string workspace) "={trust_level=\"trusted\"}}")
                       "--sandbox" "workspace-write" "--ask-for-approval" "on-request"))))
    (setq eam-terminal-gui-session
          (eam-terminal-start (concat provider " resumed GUI") (eam--executable provider) args workspace))))
(defun ai-validation-gui-claude () (interactive) (ai-validation-gui-resume "claude"))
(defun ai-validation-gui-codex () (interactive) (ai-validation-gui-resume "codex"))
(defun ai-validation-gui-editor-append ()
  (interactive)
  (unless server-buffer-clients (user-error "Not a CLI external editor buffer"))
  (goto-char (point-max)) (insert "\nGUI 왕복 확인"))
(defun ai-validation-gui-editor-done ()
  (interactive)
  (unless server-buffer-clients (user-error "Not a CLI external editor buffer"))
  (save-buffer) (server-edit))
(easy-menu-define ai-validation-gui-menu global-map "Recorded CLI test sessions."
  '("Validation"
    ["Resume tested Claude" ai-validation-gui-claude t]
    ["Resume tested Codex" ai-validation-gui-codex t]
    ["Append editor test text" ai-validation-gui-editor-append server-buffer-clients]
    ["Return editor to CLI" ai-validation-gui-editor-done server-buffer-clients]))
