;;; terminal-display-test.el --- Glyph and color regressions -*- lexical-binding: t; -*-
(require 'ert)
(require 'eam-terminal)

(ert-deftest eam-display-animated-colors-reset-without-stale-faces ()
  (with-temp-buffer
    (let ((term (ghostel--new 4 60 1000)))
      (dotimes (i 200)
        (ghostel--write-vt term
          (format "\e[?2026h\e[H\e[38;2;%d;40;60;48;2;30;30;30m⠁GLITTER\e[0m plain\e[K\e[?2026l"
                  (mod i 256)))
        ;; Also exercise batching: several frames consumed before rendering.
        (when (zerop (% i 7))
          (ghostel--redraw term)
          (should (equal (plist-get (get-text-property 1 'face) :foreground)
                         (format "#%02x283c" i)))
          (should-not (get-text-property 10 'face))))
      (ghostel--write-vt term "\e[H\e[0mRESET\e[K")
      (ghostel--redraw term)
      (should (string-prefix-p "RESET\n" (buffer-string)))
      (dotimes (i 5) (should-not (get-text-property (1+ i) 'face))))))

(ert-deftest eam-display-braille-font-preserves-colors-and-normal-text ()
  (skip-unless (and (display-graphic-p) (find-font (font-spec :family "Apple Symbols"))))
  (save-window-excursion
    (with-temp-buffer
      (set-window-buffer (selected-window) (current-buffer))
      (insert (propertize "A한⠁⠀⣿ Z" 'face '(:foreground "#aabbcc" :background "#1e1e1e")))
      (put-text-property 5 6 'display '((min-width (2)) (height 1.0)))
      (put-text-property 6 7 'display '(space :width 0))
      (let ((eam-terminal-braille-font "Apple Symbols"))
        (eam-terminal-display--braille (point-min) (point-max)))
      (should-not (plist-get (get-text-property 1 'face) :family))
      (should-not (get-text-property 2 'display))
      (dolist (pos '(3 4 5))
        (should (equal (format "%s" (font-get (font-at pos) :family)) "Apple Symbols"))
        (should (equal (plist-get (get-text-property pos 'face) :foreground) "#aabbcc"))
        (should (equal (car (get-text-property pos 'display)) '(min-width (1)))))
      (should-not (get-text-property 6 'display)))))

(ert-deftest eam-display-correction-is-scoped-and-respects-opt-out ()
  (with-temp-buffer
    (let ((ghostel--repainted-region '(1 . 1))
          (eam-terminal-braille-font "Apple Symbols")
          (eam-terminal--current nil)
          (calls 0))
      (cl-letf (((symbol-function 'eam-terminal-display--braille)
                 (lambda (&rest _) (cl-incf calls))))
        (should (eam-terminal-display--redraw (lambda () t)))
        (should (= calls 0))
        (setq eam-terminal--current t)
        (should-not (eam-terminal-display--redraw (lambda () nil)))
        (should (= calls 0))
        (should (eam-terminal-display--redraw (lambda () t)))
        (should (= calls 1))
        (setq eam-terminal-braille-font nil)
        (should (eam-terminal-display--redraw (lambda () t)))
        (should (= calls 1))))))
