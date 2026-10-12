# Hadanion

Aqua and Octo, expressive 3D desktop companions for [Hadalis](https://github.com/llocphann/Hadalis). Companion is an optional installation, disabled by default in Hadalis.

This repository owns the procedural liquid renderer, Blender models and motion curves, behavior daemon, expressions, movement, curiosity, alternating characters, orbital cloud actions, chat, check-ins and Companion settings. Hadalis supplies the Abyss surfaces, theme, shared AI transport, Obsidian/Todo integration and a small optional host adapter.

## Integrated installation via Alis

**Alis users do not install or update Companion separately.** When installed
through Alis `./setup install`, `reinstall` or `update`, Alis fetches a
known-compatible, SHA-pinned revision of this separate source repository and
runs the user-owned Companion release installer. A no-op Alis update also
repairs a missing Companion. Installation is automatic; whether Aqua or Octo
is **enabled** remains a separate user preference in **Settings → Companion**.

The integration currently covers Alis's user `./setup` flows. Arch/Nix and
staged system `make install` packaging must not build this project as root or
write to an arbitrary user's home; their first-user provisioning still needs
a separately validated packaging route.

Rust 1.95 or newer is required to build the native behavior daemon;
rendering uses Alis's Quickshell/Qt environment. Blender is only for
authoring. No LLM is downloaded or loaded by the installer.
**Super + Alt + Comma** retains its existing chat behavior.

### Independent development

For development and release testing of the standalone source checkout:

```sh
git clone https://github.com/llocphann/Alis-Companion.git
cd Alis-Companion
make install
inir ipc hadanion refresh
```

Normal Alis users should use its integrated setup/update lifecycle instead.

The runtime is installed to `${XDG_DATA_HOME:-$HOME/.local/share}/hadanion/releases/<commit>`. An atomic `current` link selects a release. The payload excludes Blender files, tests, documentation and models. Hadalis discovers this user installation without modifying its source tree or a system package.

## Update and remove

```sh
git pull --ff-only
make install
inir ipc hadanion refresh

make uninstall
inir ipc hadanion refresh
```

Uninstall detaches the runtime. Preferences, chat history, Obsidian notes and previous releases remain intact. Alis's managed setup/update now installs its pinned Companion revision automatically; standalone development remains manual. The `abyss.companion` / `abyss.companionMind` preferences, `wull` IPC target, transcript path and `inir-companiond` binary name remain compatible during extraction.

## Development

```sh
make build
make test HADALIS_ROOT=/path/to/Hadalis
# Include the optional Obsidian package for journal regression checks:
make test HADALIS_ROOT=/path/to/Hadalis HADALIRD_ROOT=/path/to/Hadalird
```

With current Hadalis hosts, Obsidian helpers belong to the optional Hadalird
package. Journal checks can stage its committed source privately:
`python3 scripts/validate.py --hadalis-root /clean/Hadalis --hadalird-root /clean/Hadalird --require-clean --only test-wull-local-mind.py test-wull-shared-ai-runtime.py test-wull-journal-ui.py WullMind.qml`.
This builds no resident service, installs nothing and changes no user settings.
Chat/history do not require Obsidian; missing journal integration returns a
bounded error only when a journal action is explicitly requested.

Validation uses an isolated host checkout and fake inference fixtures; it does not modify the live desktop or call a real LLM. See the [documentation index](docs/README.md) for host API, extraction provenance, renderer runbooks and Mochi × Mak1zu × Hadalis decisions. **All active tasks and milestones are now managed centrally in [Alis dev](https://github.com/llocphann/Alis/blob/dev/to-do/README.md)**; the former visual/AI TODO links are compatibility pointers and historical receipts remain at the source-pinned pre-centralization commit; [imported historical milestones](to-do/archive/README.md) and [retired research](docs/archive/README.md) are frozen archives, not current implementation instructions. Historical Hadalis evidence remains source-pinned and is not a test result for this repository.

License: GPL-3.0-or-later; imported source attribution is retained.
