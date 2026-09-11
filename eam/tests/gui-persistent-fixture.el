;;; gui-persistent-fixture.el --- Bounded persistent GUI fixture -*- lexical-binding: t; -*-
(require 'eam-app)
(require 'eam-persistent)
(let* ((root (file-name-as-directory (getenv "EMACS_AI_PERSISTENT_FIXTURE")))
       (session (expand-file-name "persistent/session" root)))
  (when (file-exists-p root) (error "Use a new fixture root"))
  (make-directory root t)
  (setq eam-directory (expand-file-name "records/" root))
  (eam-persistent--call
   "start" `((session . ,session) (provider . "GUI-Fake")
             (executable . ,(executable-find "python3"))
             (args . ["-u" "-c" "import os,time; print('GUI PERSISTENT READY pid='+str(os.getpid())); print('한글 화면 유지 / no AI'); time.sleep(1800)"])
             (directory . ,root)))
  ;; Safety net for this fixture only, in addition to explicit test cleanup.
  (run-at-time 900 nil (lambda () (eam-persistent--call "stop" `((session . ,session)))))
  (eam-keys-mode 1)
  (eam-attach session)
  (delete-other-windows))
