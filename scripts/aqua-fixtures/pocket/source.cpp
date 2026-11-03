#include <QApplication>
#include <QDrag>
#include <QJsonArray>
#include <QJsonDocument>
#include <QMimeData>
#include <QMouseEvent>
#include <QTimer>
#include <QWindow>
#include <QUrl>
#include <QWidget>
#include <dlfcn.h>
#include <sys/mman.h>
#include <unistd.h>
#include <cstdint>
#include <cstring>
#include <iostream>

namespace {
using TargetFn = void(*)(void *, void *, const char *);
using VoidFn = void(*)(void *, void *);
using ActionFn = void(*)(void *, void *, uint32_t);
TargetFn g_orig_target = nullptr;
VoidFn g_orig_cancelled = nullptr;
VoidFn g_orig_drop = nullptr;
ActionFn g_orig_action = nullptr;

void handle_action_compat(void *data, void *object, uint32_t dnd_action) {
    if (g_orig_target)
        g_orig_target(data, object, dnd_action ? "text/uri-list" : nullptr);
    if (g_orig_action)
        g_orig_action(data, object, dnd_action);
}

void handle_cancelled_compat(void *data, void *object) {
    if (g_orig_target)
        g_orig_target(data, object, nullptr);
    if (g_orig_action)
        g_orig_action(data, object, 0);
    if (g_orig_drop)
        g_orig_drop(data, object);
    if (g_orig_cancelled)
        g_orig_cancelled(data, object);
}

void install_wayland_action_compat() {
    static const char kUriMime[] = "text/uri-list";
    if (auto *portal_fn = dlsym(RTLD_DEFAULT, "_ZN29QXdgDesktopPortalFileTransfer20fileTransferMimeTypeEv")) {
        auto ppage = reinterpret_cast<uintptr_t>(portal_fn) & ~static_cast<uintptr_t>(0xfff);
        if (mprotect(reinterpret_cast<void *>(ppage), 4096, PROT_READ | PROT_WRITE | PROT_EXEC) == 0) {
            uint8_t patch[16] = {0xb8, 0x0d, 0x00, 0x00, 0x00, 0x48, 0xba};
            uintptr_t ptr = reinterpret_cast<uintptr_t>(kUriMime);
            std::memcpy(patch + 7, &ptr, sizeof(ptr));
            patch[15] = 0xc3;
            std::memcpy(portal_fn, patch, sizeof(patch));
            mprotect(reinterpret_cast<void *>(ppage), 4096, PROT_READ | PROT_EXEC);
        }
    }
    void *wl_handle = dlopen("libQt6WaylandClient.so.6", RTLD_LAZY | RTLD_NOLOAD);
    if (!wl_handle)
        wl_handle = dlopen("libQt6WaylandClient.so.6", RTLD_LAZY);
    auto **slots = reinterpret_cast<void **>(
        dlsym(wl_handle ? wl_handle : RTLD_DEFAULT, "_ZN9QtWayland14wl_data_source25m_wl_data_source_listenerE"));
    if (!slots || !slots[0] || !slots[2] || !slots[3] || !slots[5])
        return;
    auto page = reinterpret_cast<uintptr_t>(slots) & ~static_cast<uintptr_t>(0xfff);
    if (mprotect(reinterpret_cast<void *>(page), 4096, PROT_READ | PROT_WRITE) != 0)
        return;
    g_orig_target = reinterpret_cast<TargetFn>(slots[0]);
    g_orig_cancelled = reinterpret_cast<VoidFn>(slots[2]);
    g_orig_drop = reinterpret_cast<VoidFn>(slots[3]);
    g_orig_action = reinterpret_cast<ActionFn>(slots[5]);
    slots[2] = reinterpret_cast<void *>(&handle_cancelled_compat);
    slots[5] = reinterpret_cast<void *>(&handle_action_compat);
}
} // namespace

// A real independent client offering Copy and Move. Never opens/removes files.
class Source : public QWidget {
    QPoint start;
    bool pressed=false;
    void mousePressEvent(QMouseEvent *event) override {
        start=event->position().toPoint();pressed=true;
    }
    void mouseMoveEvent(QMouseEvent *event) override {
        if (!pressed || !(event->buttons() & Qt::LeftButton)
                || (event->position().toPoint()-start).manhattanLength()<12) return;
        pressed=false;
        auto *drag=new QDrag(this);
        auto *mime=new QMimeData;
        QList<QUrl> urls;
        for (auto value:QJsonDocument::fromJson(qgetenv("POCKET_SOURCE_URLS")).array())
            urls.append(QUrl(value.toString()));
        mime->setUrls(urls);drag->setMimeData(mime);
        auto supported=qgetenv("POCKET_SOURCE_ACTIONS")=="move-only"
            ? Qt::DropActions(Qt::MoveAction) : Qt::CopyAction|Qt::MoveAction;
        std::cout<<"POCKET_DRAG_STARTED"<<std::endl;
        auto action=drag->exec(supported,Qt::MoveAction);
        const char *name=action==Qt::CopyAction?"CopyAction":action==Qt::MoveAction?"MoveAction":"IgnoreAction";
        std::cout<<"{\"result\":\""<<name<<"\"}"<<std::endl;
        drag->deleteLater();
    }
public:
    Source() {
        setWindowTitle("Private Companion Pocket drag source");
        setStyleSheet("background-color:#101b22");
    }
};
int main(int argc,char **argv) {
    QApplication app(argc,argv);
    install_wayland_action_compat();
    Source source;source.showFullScreen();
    QTimer ready;
    QObject::connect(&ready,&QTimer::timeout,[&] {
        if (source.windowHandle() && source.windowHandle()->isExposed()) {
            ready.stop();std::cout<<"POCKET_SOURCE_READY"<<std::endl;
        }
    });
    ready.start(25);
    return app.exec();
}
