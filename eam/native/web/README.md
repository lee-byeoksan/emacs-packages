# Embedded remote terminal UI

`eam-runtime serve` embeds these files with `include_bytes!`. No npm, CDN or Python is required at runtime. `eam-native--cache` includes this directory in its build digest; the install tar includes it too.

The prototype UI was moved from `experiments/remote-web`. Make production UI changes here.

Vendored dependencies (MIT, licenses in `vendor/`):

- `@xterm/xterm` 6.0.0: `lib/xterm.js`, `css/xterm.css`
- `@xterm/addon-fit` 0.11.0: `lib/addon-fit.js` → `vendor/fit.js`

The exact npm integrity hashes are recorded in `experiments/remote-web/package-lock.json`. Source maps are not served. When upgrading, copy the distribution files and licenses, review upstream changes, rebuild the runtime and run the remote browser integration test.

Local patch in `vendor/fit.js`: the FitAddon 0.11.0 scrollbar width reservation is
zero instead of 14px. xterm 6 already positions its scrollbar over the screen;
our CSS narrows that overlay, and the fit calculation uses the entire width.
Preserve this patch when upgrading, or use an upstream overlay option if available.
