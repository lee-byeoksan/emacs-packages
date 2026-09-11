;;; package-vc-compile.el --- Check VC compiler exclusions -*- lexical-binding: t; -*-
(require 'ert)
(require 'package)
(ert-deftest eam-vc-compile-excludes-nonproduct-files ()
  (let* ((root (make-temp-file "eam-compile-" t))
         (source (expand-file-name "../.elpaignore"
                                   (file-name-directory (or load-file-name buffer-file-name))))
         (descriptor (package-desc-create :name 'eam :version '(0 1 0) :dir root :kind 'vc)))
    (unwind-protect
        (progn
          (copy-file source (expand-file-name ".elpaignore" root))
          (dolist (name '("tests/bad.el" "experiments/nested/bad.el"
                          "examples/bad.el" "docs/nested/bad.el" "var/bad.el"
                          "lisp/eam-standalone.el"))
            (let ((path (expand-file-name name root)))
              (make-directory (file-name-directory path) t)
              (write-region "(invalid . data)" nil path nil 'silent)))
          (write-region ";;; -*- lexical-binding: t; -*-\n(defun eam-test-product () t)\n"
                        nil (expand-file-name "lisp/product.el" root) nil 'silent)
          (package--compile descriptor)
          (should (equal (mapcar (lambda (f) (file-relative-name f root))
                                (directory-files-recursively root "\\.elc\\'"))
                         '("lisp/product.elc"))))
      (delete-directory root t))))
(ert-deftest eam-monorepo-vc-compile-excludes-nonproduct-files ()
  (let* ((root (make-temp-file "eam-monorepo-compile-" t))
         (source (expand-file-name "../../.elpaignore"
                                   (file-name-directory (or load-file-name buffer-file-name))))
         (descriptor (package-desc-create :name 'eam :version '(0 1 0) :dir root :kind 'vc)))
    (unwind-protect
        (progn
          (copy-file source (expand-file-name ".elpaignore" root))
          (dolist (name '("eam/tests/bad.el" "eam/experiments/nested/bad.el"
                          "eam/examples/bad.el" "eam/docs/nested/bad.el"
                          "eam/var/bad.el" "eam/lisp/eam-standalone.el"))
            (let ((path (expand-file-name name root)))
              (make-directory (file-name-directory path) t)
              (write-region "(invalid . data)" nil path nil 'silent)))
          (write-region ";;; -*- lexical-binding: t; -*-\n(defun eam-monorepo-product () t)\n"
                        nil (expand-file-name "eam/lisp/product.el" root) nil 'silent)
          (package--compile descriptor)
          (should (equal (mapcar (lambda (f) (file-relative-name f root))
                                (directory-files-recursively root "\\.elc\\'"))
                         '("eam/lisp/product.elc"))))
      (delete-directory root t))))
(ert-run-tests-batch-and-exit)
