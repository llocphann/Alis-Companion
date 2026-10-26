# Companion tools and regression entry point

Run `python3 scripts/validate.py --alis-root /path/to/Alis` (or `make test ALIS_ROOT=...`) for current product checks. `--require-clean` requires committed source; development `--only` selects focused checks. The validator prints both repository revisions and separately reports skipped environment checks.

The installer stages product files only. The validator assembles an isolated host for shared Alis dependencies and uses fake providers/model servers. It does not install into the live desktop, call a real model or write real journals.

`aqua-author-motion.py` / `companion-author-models.py` and the Blender verification/preview tools own the authoring workflow. Original private diagnostic, receipt and qualification tools were transferred for historical continuity. Their old source SHA/path guards remain authoritative; they are not automatic acceptance gates for Alis-Companion and must not replay consumed evidence. Use the current validator for extraction/product changes.
