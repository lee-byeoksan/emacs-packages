;;; session-selector-benchmark.el --- Local selector timings -*- lexical-binding: t; -*-
(require 'eam-app)
(require 'json)

(defun eam-selector-benchmark ()
  "Measure candidate construction with real local Git; never start an AI CLI."
  (let ((root (make-temp-file "eam-selector-한글 " t))
        (eam-terminal--sessions nil)
        buffers results)
    (unwind-protect
        (progn
          (dotimes (index 100)
            (let* ((directory (expand-file-name (format "프로젝트 %03d" index) root))
                   (buffer (generate-new-buffer (format "*selector-%03d*" index))))
              (make-directory directory)
              (let ((default-directory (file-name-as-directory directory)))
                (unless (eq 0 (process-file "git" nil nil nil "init" "--quiet"))
                  (error "Fixture Git init failed"))
                (unless (eq 0 (process-file "git" nil nil nil "symbolic-ref" "HEAD"
                                           "refs/heads/feature/한글-긴-브랜치"))
                  (error "Fixture branch failed")))
              (push buffer buffers)
              (push (eam-terminal--session :name "Codex" :directory directory
                                                :output buffer)
                    eam-terminal--sessions)
              (when (memq (1+ index) '(1 10 30 100))
                (let (samples verified)
                  (dotimes (_ 5)
                    (garbage-collect)
                    (let* ((start (float-time))
                           (candidates (eam-app--session-candidates))
                           (elapsed (- (float-time) start)))
                      (unless (= (length candidates) (1+ index))
                        (error "Lost candidates"))
                      (push (cl-count-if
                             (lambda (entry)
                               (string-match-p "branch:feature/한글-긴-브랜치" (car entry)))
                             candidates) verified)
                      (dolist (entry candidates)
                        (unless (string-match-p
                                 "branch:\\(?:feature/한글-긴-브랜치\\|unverified\\) |" (car entry))
                          (error "Incorrect branch metadata: %s" (car entry))))
                      (push elapsed samples)))
                  (push `((projects . ,(1+ index))
                          (seconds . ,(vconcat (nreverse samples)))
                          (verified-branches . ,(vconcat (nreverse verified)))) results)))))
          (princ (json-encode `((emacs . ,emacs-version)
                                (scope . "batch candidate construction; no GUI or AI")
                                (results . ,(vconcat (nreverse results)))))))
      (mapc #'kill-buffer buffers)
      (delete-directory root t))))

(when noninteractive (eam-selector-benchmark))
