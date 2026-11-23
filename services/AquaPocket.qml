pragma Singleton
import QtQuick
import Quickshell
import Quickshell.Io
import qs.services
import qs.modules.common
import "AquaPocketPolicy.js" as Policy

Singleton {
    id: root
    // The owning session supplies visibility/lock/output/interaction consent.
    property bool hostAvailable: false
    readonly property bool permitted: hostAvailable && Config.ready
        && Config.options?.abyss?.companion?.pocketEnabled === true
    property bool opened: false
    property bool busy: false
    property bool draining: false
    property var items: []
    property string notice: ""
    property var pending: null
    property int epoch: 0
    readonly property bool canSelect: permitted && !busy && !draining
    signal filesSwallowed(int count)

    function close(): void {opened=false}
    function revoke(): void {
        epoch++;pending=null;busy=false;opened=false;items=[];notice="";deadline.stop()
        if (worker.running) {draining=true;worker.running=false}
    }
    onPermittedChanged: if (!permitted) revoke()
    function dispatch(action, extra): bool {
        if (!canSelect || !AlisCompanion.available) return false
        notice=""
        pending={epoch:++epoch,action:action,request:Object.assign({version:1,action:action,consent:true},extra ?? {})}
        busy=true;worker.startObserved=false;worker.running=true
        return true
    }
    function show(): bool {
        if (!canSelect) return false
        opened=true
        return dispatch("list",null)
    }
    function refresh(): bool {
        if (!canSelect) return false
        return dispatch("list",null)
    }
    function addFiles(values, source, openFloating = true): bool {
        const selection=Policy.urls(values)
        if (!selection || !["picker","drop"].includes(source)) {
            notice="Choose up to eight local files at a time."
            return false
        }
        if (!dispatch("add",{urls:selection,source:source})) return false
        if (source === "picker") {
            if (openFloating !== false)
                opened=true
            filesSwallowed(selection.length)
        }
        return true
    }
    function remove(id): bool {
        return Number.isSafeInteger(id) && id>0 && dispatch("remove",{id:id})
    }
    function clear(confirmed): bool {return confirmed===true && dispatch("clear",{confirmed:true})}
    function completed(raw, code, serial): void {
        const job=pending
        if (!permitted || !job || job.epoch!==serial || serial!==epoch) return
        pending=null;busy=false;deadline.stop()
        const response=Policy.reply(raw,job.action,code)
        if (!response) {notice=Policy.error(raw);return}
        items=response.items
        notice=job.action==="add" ? "References saved. File contents stay private."
            : job.action==="remove" || job.action==="clear" ? "References removed. Original files are unchanged." : ""
    }
    Timer {
        id:deadline;interval:6000
        onTriggered: {
            root.epoch++;root.pending=null;root.busy=false
            root.notice="Pocket timed out. Reopen Pocket to refresh saved references."
            if (worker.running) {root.draining=true;worker.running=false}
        }
    }
    Timer {
        id: workerFlushTimer
        interval: 120
        repeat: false
        onTriggered: {
            if (worker._exited && !worker._finalized) {
                worker._stdoutDone = true
                worker._maybeFinalize()
            }
        }
    }
    Process {
        id: worker; running: false; stdinEnabled: true
        property bool startObserved: false
        property int serial: 0
        property bool _exited: false
        property int _exitCode: 0
        property bool _stdoutDone: false
        property bool _finalized: false
        command: [AlisCompanion.packageRoot + "/scripts/native-dispatch", "companion", "--pocket"]
        stdout: StdioCollector {
            id: reply
            onStreamFinished: {
                worker._stdoutDone = true
                worker._maybeFinalize()
            }
        }
        onStarted: {
            startObserved = true; serial = root.pending?.epoch ?? -1
            worker._exited = false
            worker._exitCode = 0
            worker._stdoutDone = false
            worker._finalized = false
            workerFlushTimer.stop()
            if (!root.permitted || !root.pending) { running = false; return }
            write(JSON.stringify(root.pending.request) + "\n"); deadline.restart()
        }
        onRunningChanged: if (!running && !startObserved && root.pending) root.completed("", -1, root.pending.epoch)
        onExited: (code, status) => {
            if (root.draining) {
                root.draining = false
                worker._finalized = true
                workerFlushTimer.stop()
                return
            }
            worker._exited = true
            worker._exitCode = code
            if (worker._stdoutDone)
                worker._maybeFinalize()
            else
                workerFlushTimer.restart()
        }
        function _maybeFinalize(): void {
            if (!worker._exited || worker._finalized || !worker._stdoutDone)
                return
            worker._finalized = true
            workerFlushTimer.stop()
            root.completed(String(reply.text ?? ""), worker._exitCode, worker.serial)
        }
    }
}
