#include <QApplication>
#include <QDrag>
#include <QJsonArray>
#include <QJsonDocument>
#include <QMimeData>
#include <QMouseEvent>
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
        auto action=drag->exec(Qt::CopyAction|Qt::MoveAction,Qt::MoveAction);
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
    std::cout<<"POCKET_SOURCE_READY"<<std::endl;
    return app.exec();
}
