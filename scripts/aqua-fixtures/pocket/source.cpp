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
#include <iostream>

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
    QApplication app(argc,argv);Source source;source.showFullScreen();
    QTimer ready;
    QObject::connect(&ready,&QTimer::timeout,[&] {
        if (source.windowHandle() && source.windowHandle()->isExposed()) {
            ready.stop();std::cout<<"POCKET_SOURCE_READY"<<std::endl;
        }
    });
    ready.start(25);
    return app.exec();
}
