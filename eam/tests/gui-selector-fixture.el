;;; gui-selector-fixture.el --- Finite real-PTY session selector fixture -*- lexical-binding: t; -*-
(require 'eam-app)
(require 'json)
(let* ((root (file-name-as-directory (getenv "EMACS_AI_SELECTOR_ROOT")))
       (eam-directory (expand-file-name "records/" root))
       (common "한글 경로와 공백이 있는 프로젝트 공통 이름-"))
  (when (file-exists-p root) (error "Use a new fixture directory"))
  (make-directory root t)
  (dolist (spec '(("Alpha" "alpha" 0) ("Beta" "beta" 0) ("Gamma" "gamma" 7)))
    (let* ((name (nth 0 spec)) (branch (nth 1 spec)) (code (nth 2 spec))
           (directory (expand-file-name (concat common name) root)))
      (make-directory directory)
      (unless (zerop (call-process "git" nil nil nil "init" "-q" "-b" branch directory))
        (error "Fixture git init failed"))
      (eam-terminal-start
       name (executable-find "python3")
       (list "-u" "-c"
             (format "import time; print('%s READY'); %s"
                     name (if (= code 0) "time.sleep(300)" "raise SystemExit(7)")))
       directory)))
  (setq default-directory root)
  (eam)
  (run-at-time 2 nil
               (lambda ()
                 (with-temp-file (expand-file-name "candidates.json" root)
                   (insert (json-encode
                            (vconcat (mapcar #'car (eam-app--session-candidates)))))))))
