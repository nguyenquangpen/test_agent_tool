import asyncio
import os
import sys

from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QListWidget, QListWidgetItem, QHBoxLayout, QStackedWidget
from qasync import QEventLoop

from pages.auths import LoginDialog
from pages.projects import ProjectsPage
from pages.viewpoints import ViewpointsPage
from pages.testcases import TestcasesPage

if getattr(sys, '_MEIPASS', None):
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = os.path.join(sys._MEIPASS, 'ms-playwright')

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Demo App")
        self.resize(1344, 756)

        container = QWidget()
        self.setCentralWidget(container)
        main_layout = QHBoxLayout(container)

        self.sidebar = QListWidget()
        for name in ("Projects", "Viewpoints"):
            QListWidgetItem(name, self.sidebar)
        self.sidebar.currentRowChanged.connect(self.switch_page)
        main_layout.addWidget(self.sidebar, 0)

        self.stack = QStackedWidget()
        main_layout.addWidget(self.stack, 1)

        self.projects_page = ProjectsPage()
        self.viewpoints_page = ViewpointsPage()
        self.testcases_page = TestcasesPage()

        self.stack.addWidget(self.projects_page)
        self.stack.addWidget(self.viewpoints_page)
        self.stack.addWidget(self.testcases_page)

        self.projects_page.expand_project.connect(self.on_project_expand)
        self.testcases_page.back_to_projects.connect(lambda: self.stack.setCurrentWidget(self.projects_page))

        self.sidebar.setCurrentRow(0)

    def on_project_expand(self, proj: dict):
        self.testcases_page.load_testcases(proj["uuid"])
        self.stack.setCurrentWidget(self.testcases_page)

    def switch_page(self, idx):
        if idx in (0, 1):
            self.stack.setCurrentIndex(idx)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    async def go():
        dlg = LoginDialog()
        if await dlg.exec_async():
            win = MainWindow()
            win.show()

    app.aboutToQuit.connect(loop.stop)

    with loop:
        loop.run_until_complete(go())
        loop.run_forever()
