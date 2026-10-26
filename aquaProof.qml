//@ pragma UseQApplication
//@ pragma Env QS_NO_RELOAD_POPUP=1
//@ pragma Env ALIS_STANDALONE_WINDOW=1

import QtQuick
import Quickshell
import qs.optional.alis_companion.modules.abyss.companion

// Development-only Aqua renderer/attachment entry point. Keep this at the
// repository root so Quickshell resolves qs.modules.* against the Alis
// shell root exactly like the shipped standalone entry points.
AbyssCompanionProof {
}
