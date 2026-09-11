;;; eam-terminal-display.el --- Terminal glyph compatibility -*- lexical-binding: t; -*-
(require 'ghostel)
(require 'eam)

(defcustom eam-terminal-braille-font
  (when (eq system-type 'darwin) "Apple Symbols")
  "Font for terminal Braille graphics, or nil to keep Emacs font fallback.
Apple Braille draws placeholder dots even for empty Braille patterns.
This setting affects only U+2800–U+28FF in EAM terminal output."
  :type '(choice (const :tag "Default fallback" nil) string) :group 'eam)

(defvar eam-terminal--current)

(defun eam-terminal-display--braille (start end)
  "Apply Braille graphics font within the repainted range START to END.
Preserve terminal colors, and fit each glyph into one terminal cell."
  (when (and eam-terminal-braille-font (display-graphic-p)
             (save-excursion (goto-char start) (re-search-forward "[⠀-⣿]" end t))
             (find-font (font-spec :family eam-terminal-braille-font)))
    (let ((inhibit-read-only t)
          (inhibit-modification-hooks t)
          (cell-width (window-font-width))
          scale)
      (save-excursion
        (goto-char start)
        (while (re-search-forward "[⠀-⣿]" end t)
          (let* ((pos (1- (point)))
                 (face (get-text-property pos 'face)))
            ;; Renderer faces are plists; keep all SGR attributes intact.
            (put-text-property pos (1+ pos) 'face
                               (plist-put (copy-sequence face) :family
                                          eam-terminal-braille-font))
            ;; Ghostel can lend a following blank cell to a wide fallback
            ;; glyph. Return that cell when fitting Braille to one column.
            (when (and (member '(min-width (2)) (get-text-property pos 'display))
                       (eq (char-after (1+ pos)) ?\s)
                       (equal (get-text-property (1+ pos) 'display) '(space :width 0)))
              (remove-text-properties (1+ pos) (+ pos 2) '(display nil)))
            (remove-text-properties pos (1+ pos) '(display nil))
            (unless scale
              (let* ((font (font-at pos))
                     (glyph (and font (aref (font-get-glyphs font 0 1 "⣿") 0)))
                     (pixels (and font (font-get font :size)))
                     (width (and glyph (aref glyph 4))))
                (setq scale
                      (if (and width pixels (> width cell-width) (> pixels 0))
                          (/ (float (max 1 (floor (* pixels (/ (float cell-width) width)))))
                             pixels)
                        1.0))))
            (put-text-property pos (1+ pos) 'display
                               `((min-width (1)) (height ,scale)))))))))

(defun eam-terminal-display--redraw (original &rest args)
  "Keep ORIGINAL renderer behavior and fix EAM Braille graphics afterward."
  (let ((rendered (apply original args)))
    (when (and rendered (bound-and-true-p eam-terminal--current)
               ghostel--repainted-region eam-terminal-braille-font)
      (with-demoted-errors "EAM Braille display: %S"
        (eam-terminal-display--braille
         (max (point-min) (car ghostel--repainted-region))
         (min (point-max) (cdr ghostel--repainted-region)))))
    rendered))

(advice-add 'ghostel--redraw :around #'eam-terminal-display--redraw)
(provide 'eam-terminal-display)
;;; eam-terminal-display.el ends here
