;;; eam-worktree.el --- Local Git worktrees and CLI sessions -*- lexical-binding: t; -*-
(require 'eam-terminal)
(require 'subr-x)

(defun eam-worktree--git (directory &rest args)
  "Run Git ARGS in local DIRECTORY, without shell interpolation or a fetch."
  (when (file-remote-p directory) (user-error "Choose a local repository"))
  ;; read-directory-name may return ~/...; Git does not expand a tilde in -C.
  (setq directory (expand-file-name directory))
  (with-temp-buffer
    (let ((status (apply #'process-file "git" nil '(t t) nil "-C" directory args)))
      (unless (eq status 0) (user-error "Git failed: %s" (string-trim (buffer-string))))
      (buffer-string))))

(defun eam-worktree--list (directory)
  "Read registered worktrees using NUL-delimited porcelain output."
  (let (entries current)
    (dolist (field (split-string (eam-worktree--git directory "worktree" "list" "--porcelain" "-z") "\0"))
      (cond ((string-prefix-p "worktree " field)
             (when current (push current entries))
             (setq current (list :path (substring field 9))))
            ((string-prefix-p "branch " field) (setq current (plist-put current :branch (substring field 7))))
            ((string-prefix-p "HEAD " field) (setq current (plist-put current :head (substring field 5))))
            ((equal field "bare") (setq current (plist-put current :bare t)))
            ((string-prefix-p "locked" field) (setq current (plist-put current :locked t)))
            ((string-prefix-p "prunable" field) (setq current (plist-put current :prunable t)))))
    (when current (push current entries))
    (nreverse entries)))

(defun eam-worktree--choose (repository)
  (let ((choices
         (mapcar (lambda (entry)
                   (cons (format "%s | %s%s" (plist-get entry :path)
                                 (or (plist-get entry :branch) "detached/bare")
                                 (if (plist-get entry :locked) " | locked" "")) entry))
                 (eam-worktree--list repository))))
    (cdr (assoc (completing-read "Worktree: " choices nil t) choices))))

(defun eam-worktree--create (repository path branch base)
  "Create a new local BRANCH at BASE without rewriting an existing branch."
  (setq path (expand-file-name path))
  (when (or (file-remote-p path) (file-exists-p path) (file-symlink-p path))
    (user-error "Choose a new local worktree path"))
  (when (or (string-empty-p branch) (string-prefix-p "-" branch))
    (user-error "Choose a valid new branch name"))
  (eam-worktree--git repository "check-ref-format" (concat "refs/heads/" branch))
  (let ((sha (string-trim (eam-worktree--git
                           repository "rev-parse" "--verify" "--end-of-options" (concat base "^{commit}")))))
    (eam-worktree--git repository "worktree" "add" "-b" branch "--" path sha))
  path)

;;;###autoload
(defun eam-worktree-create (repository path branch base)
  "Create a worktree with a new branch from an explicitly chosen local base.
No fetch, dependency install, context insertion, or AI request is performed."
  (interactive (let* ((repo (read-directory-name "Repository: " nil nil t))
                      (path (read-directory-name "New worktree path: "))
                      (branch (read-string "New branch: "))
                      (base (read-string "Local base ref/SHA (no fetch): " "HEAD")))
                 (list repo path branch base)))
  (dired (eam-worktree--create repository path branch base)))

;;;###autoload
(defun eam-worktree-open (repository)
  "Select a registered worktree and browse its files."
  (interactive (list (read-directory-name "Repository: " nil nil t)))
  (let ((entry (eam-worktree--choose repository)))
    (unless (and entry (not (plist-get entry :bare)) (file-directory-p (plist-get entry :path)))
      (user-error "Worktree is unavailable or bare"))
    (dired (plist-get entry :path))))

;;;###autoload
(defun eam-worktree-start (repository)
  "Choose a registered worktree and explicitly start one native CLI there."
  (interactive (list (read-directory-name "Repository: " nil nil t)))
  (let* ((entry (eam-worktree--choose repository))
         (path (plist-get entry :path)))
    (unless (and path (not (plist-get entry :bare)) (file-directory-p path))
      (user-error "Choose an available non-bare worktree"))
    (eam-terminal-start-provider (eam--read-provider) path)))

(defun eam-worktree--same-or-inside (child parent)
  (and child (not (file-remote-p child))
       (let ((child (file-name-as-directory (file-truename child)))
             (parent (file-name-as-directory (file-truename parent))))
         (string-prefix-p parent child))))

(defvar eam-worktree-before-remove-hook nil
  "Functions called with a worktree path before any deletion; may reject it.")

(defun eam-worktree--remove (repository path)
  "Remove one clean secondary worktree, preserving its branch.
Reject live app sessions, modified visiting buffers, untracked and ignored files."
  (let* ((entries (eam-worktree--list repository))
         (target (cl-find-if (lambda (entry) (equal (file-truename path)
                                                    (file-truename (plist-get entry :path)))) entries)))
    (unless (and target (not (eq target (car entries)))
                 (not (plist-get target :bare)) (not (plist-get target :locked))
                 (not (plist-get target :prunable)) (file-directory-p path))
      (user-error "Only an available unlocked secondary worktree can be removed"))
    (run-hook-with-args 'eam-worktree-before-remove-hook path)
    (when (cl-some (lambda (s) (and (processp (eam-terminal-process s))
                                   (process-live-p (eam-terminal-process s))
                                   (eam-worktree--same-or-inside (eam-terminal-directory s) path)))
                   eam-terminal--sessions)
      (user-error "A CLI session is running in this worktree"))
    (when (cl-some (lambda (buffer)
                     (let ((process (get-buffer-process buffer)))
                       (and process (process-live-p process)
                            (eam-worktree--same-or-inside
                             (buffer-local-value 'default-directory buffer) path))))
                   (buffer-list))
      (user-error "An Emacs process is running in this worktree"))
    (when (cl-some (lambda (buffer)
                     (with-current-buffer buffer
                       (and (buffer-modified-p) (eam-worktree--same-or-inside buffer-file-name path))))
                   (buffer-list))
      (user-error "A modified file buffer belongs to this worktree"))
    (unless (string-empty-p (eam-worktree--git path "status" "--porcelain" "-z"
                                                   "--untracked-files=all" "--ignored"))
      (user-error "Worktree has changes, untracked, or ignored files"))
    (let* ((common (string-trim (eam-worktree--git repository "rev-parse" "--path-format=absolute" "--git-common-dir")))
           (default-directory temporary-file-directory))
      (with-temp-buffer
        (unless (eq 0 (process-file "git" nil '(t t) nil (concat "--git-dir=" common)
                                    "worktree" "remove" "--" (plist-get target :path)))
          (user-error "Git refused removal: %s" (string-trim (buffer-string))))))))

;;;###autoload
(defun eam-worktree-remove (repository)
  "Choose and confirm removal of one clean secondary worktree."
  (interactive (list (read-directory-name "Repository: " nil nil t)))
  (let* ((entry (eam-worktree--choose repository)) (path (plist-get entry :path)))
    (unless path (user-error "No worktree selected"))
    (when (yes-or-no-p (format "Remove worktree %s (branch is kept)? " path))
      (eam-worktree--remove repository path)
      (message "Removed worktree %s; branch retained" path))))
;;;###autoload
(dolist (command '(eam-worktree-create
                    eam-worktree-open
                    eam-worktree-remove
                    eam-worktree-start))
  (put command 'completion-predicate #'ignore))

(provide 'eam-worktree)
;;; eam-worktree.el ends here
