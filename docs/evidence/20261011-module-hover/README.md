# Companion module hover input diagnosis

Task status belongs to Alis `to-do/cloud-bot/ISSUES.md`, AC-I04.
This directory records technical evidence only.

On Companion baseline `d80553ac7718b6c01f62d34877a511502d520db3`
and host `413c64be084225d0d4d01f5980652701cdce8681`, the actual
later, full-output `companionPointer` HoverHandler prevents Clock's
MouseArea/anchor hover from opening its mature popup. The actor is far
from that source. Disabling only this handler restores hover; disabling
PointHandler, actor hover, TapHandler or DragHandler does not.

Reparenting that observer to the existing host ancestor restores the
module and anchor while the observer still receives the exact scene
coordinates. Restoring its sibling parent recreates the failure. The
fix uses that ancestor and returns QObject ownership to the dying
package output on teardown: an earlier candidate leaked the observer
when the package was disabled. The finite native input mask, layer,
keyboard policy, actor, shaders, model and movement code are unchanged.

`working-matrix.log` records 36 cases: four edges, three modules, and
package disabled / actor present / actor absent at phase entry. The
fixture checks actual Qt input dispatch through the module, painted
connector outside the source/body, popup content, reversal and exit;
also observer coordinates, actual accepted clicks and package teardown.
It passed 946 checks and 49 actual field flushes, process/script exit 0.
Private Performance rendering has actor animations enabled, AI/journal/
speech disabled, and no owner installation or settings changes.

The test uses polished real hit geometry between pointer moves. An
earlier capture-after-every-move diagnostic failed the actor inputReady
deadline on a later edge; that failure is retained and its exact cause
is not diagnosed. This is an input regression fixture, not frame pacing or
visual qualification. Vertical Media intentionally pins its popup on
LeftButton; its separate Top-window outside catcher is tested directly.
Dispatching that click into the masked field would be an invalid oracle.

Commands:

```sh
cargo build --locked --release --workspace --manifest-path native/Cargo.toml
ALIS_COMPANION_TEST_HOST=/path/to/Alis python3 scripts/test-module-hover-runtime.py
python3 scripts/validate.py --alis-root /path/to/Alis \
  --only test-module-hover-runtime.py --require-clean
```

The receipt pins source bytes and the unchanged native tree/binary.
The subsequent `exact-pair.json`/`exact-pair.log` record focused PASS
on Companion `a49284fa36cf030d3b2ab4212ecf7efebc63edf3` and host
`b543bb7602eb74901c3c137c89c70c301e12fde4`, exit 0, with clean source
before and after. This is one selected check, not the full package suite.
QtTest dispatch bypasses compositor `wl_pointer` delivery. Physical
pointer/focus, Wi-Fi/Bluetooth, scaling, multiple outputs/hotplug and
owner drag/chat/animated acceptance remain separate. No Companion G0
lineage was replayed and no renderer/capture oracle was changed.
