;;; -*- lexical-binding: t; -*-
(require 'ert)
(require 'magit)
(require 'eam-app)
(ert-deftest eam-review-real-magit-renamed-file ()
  (let* ((directory (make-temp-file "review-magit-한글 " t))
         (default-directory (file-name-as-directory directory))
         (old "이전 이름.txt") (new "새 이름.txt") buffer)
    (unwind-protect
        (progn
          (should (= 0 (process-file "git" nil nil nil "init" "--quiet")))
          (with-temp-file old (dotimes (n 40) (insert (format "line %d\n" n))))
          (should (= 0 (process-file "git" nil nil nil "add" "--" old)))
          (should (= 0 (process-file "git" nil nil nil "-c" "user.name=Review test"
                                     "-c" "user.email=review@example.invalid"
                                     "-c" "core.hooksPath=/dev/null" "-c" "commit.gpgsign=false"
                                     "commit" "--quiet" "-m" "fixture")))
          (should (= 0 (process-file "git" nil nil nil "mv" "--" old new)))
          (with-temp-file new
            (dotimes (n 40) (insert (if (= n 20) "changed 한글\n" (format "line %d\n" n)))))
          (should (= 0 (process-file "git" nil nil nil "add" "--" new)))
          (magit-diff-staged nil '("--find-renames"))
          (setq buffer (current-buffer))
          (goto-char (point-min))
          (should (search-forward "+changed 한글" nil t))
          (beginning-of-line)
          (let* ((beg (point)) (end (line-end-position))
                 (span (car (eam-review--locations beg end))))
            (should (equal (plist-get span :old-file) old))
            (should (equal (plist-get span :new-file) new))
            (should (= (plist-get span :new-start) 21))
            (should (equal (plist-get (eam-review--capture beg end) :text) "+changed 한글"))
            (let ((section (magit-current-section)))
              (magit-section-hide section)
              (should-error (eam-review--capture beg end) :type 'user-error)
              (magit-section-show section)
              (should (equal (plist-get (eam-review--capture beg end) :text) "+changed 한글")))))
      (when (buffer-live-p buffer) (kill-buffer buffer))
      (dolist (b (buffer-list))
        (when (with-current-buffer b
                (and (derived-mode-p 'magit-mode) (equal default-directory (file-name-as-directory directory))))
          (kill-buffer b)))
      (delete-directory directory t))))
