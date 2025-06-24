import base64

import httpx
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QHBoxLayout, QPushButton,
    QLabel, QScrollArea, QDialog, QCheckBox, QSpacerItem, QSizePolicy
)
from qasync import asyncSlot

from cwagent.ais.agent_run import Agent
from pages.auths import access_token
from utils import base_url, fmt_datetime


class ClickableLabel(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class TestcasesPage(QWidget):
    back_to_projects = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.testcases = []
        self._expanded_row = None

    def _build_ui(self):
        self.layout = QVBoxLayout(self)
        header = QHBoxLayout()

        btn_back = QPushButton("← Back to Projects")
        btn_back.setFixedHeight(30)
        btn_back.clicked.connect(self.back_to_projects.emit)
        header.addWidget(btn_back)
        header.addStretch()
        btn_run_selected = QPushButton("Run Selected Testcases")
        btn_run_selected.setFixedHeight(30)
        btn_run_selected.clicked.connect(self.run_selected_testcases)
        header.addWidget(btn_run_selected)

        header.addStretch()
        self.layout.addLayout(header)
        self.tc_table = QTableWidget(0, 9, self)
        self.tc_table.setHorizontalHeaderLabels([
            "☑", "Field", "Type", "Testcase", "Reference", "Run status", "Created at", "Last updated", "Actions"
        ])
        hdr = self.tc_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Fixed)
        self.tc_table.setColumnWidth(0, 5)
        hdr.setSectionResizeMode(6, QHeaderView.Fixed)
        self.tc_table.setColumnWidth(6, 95)
        hdr.setSectionResizeMode(7, QHeaderView.Fixed)
        self.tc_table.setColumnWidth(7, 95)
        for col in (1, 2, 3, 4, 5, 8):
            hdr.setSectionResizeMode(col, QHeaderView.Stretch)

        self.layout.addWidget(self.tc_table)

    def load_testcases(self, project_id: str):
        try:
            with httpx.Client() as client:
                resp = client.get(
                    f"{base_url}/api/testcases?project_id={project_id}",
                    headers={"Authorization": f"Bearer {access_token[-1]}"}
                )
            self.testcases = resp.json().get("data", [])
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error loading test cases",
                f"Could not load test cases for project {project_id}:\n{e}"
            )
            return

        self.tc_table.setRowCount(0)
        for row, tc in enumerate(self.testcases):
            self.tc_table.insertRow(row)
            chk = QCheckBox()
            self.tc_table.setCellWidget(row, 0, chk)

            for col_offset, key in enumerate(
                ("test_field", "test_type", "test_case", "test_reference", "test_status", "created_at", "updated_at"),
                start=1
            ):
                raw = tc.get(key, "")

                if key in ("created_at", "updated_at"):
                    text = fmt_datetime(raw)
                else:
                    text = str(raw)

                self.tc_table.setItem(row, col_offset, QTableWidgetItem(str(text)))

            cell = QWidget()
            box = QHBoxLayout(cell)
            box.setContentsMargins(0, 0, 0, 0)

            btn_expand = QPushButton("▶")
            btn_expand.setFixedWidth(30)
            btn_expand.clicked.connect(lambda _, t=tc: self.on_expand(t))

            btn_edit = QPushButton("✎")
            btn_edit.setFixedWidth(30)
            btn_show = QPushButton("📄")
            btn_show.setFixedWidth(30)
            btn_delete = QPushButton("❌")
            btn_delete.setFixedWidth(30)

            btn_show.clicked.connect(lambda _, t=tc, r=row: self.on_show_images(t, r))

            for w in (btn_expand, btn_edit, btn_show, btn_delete):
                box.addWidget(w)
            box.addStretch()

            self.tc_table.setCellWidget(row, 8, cell)

    @asyncSlot()
    async def run_selected_testcases(self):
        selected = [
            tc
            for row, tc in enumerate(self.testcases)
            if isinstance(self.tc_table.cellWidget(row, 0), QCheckBox)
               and self.tc_table.cellWidget(row, 0).isChecked()
        ]

        if self._expanded_row is not None:
            self.tc_table.removeRow(self._expanded_row + 1)
            self._expanded_row = None

        for tc in selected:
            await self.on_expand(tc)

    @asyncSlot(dict)
    async def on_expand(self, tc: dict):
        agent = Agent(test_uuid=tc["uuid"], auth_token=access_token[-1])
        result_images = await agent.run()

        for tcd in self.testcases:
            if tcd["uuid"] == tc["uuid"]:
                tcd.update({"images": result_images})

    def on_show_images(self, tc: dict, row: int):
        images = tc.get("images", [])
        if not images:
            QMessageBox.information(self, "No images", "This test case has no images.")
            return

        if self._expanded_row == row:
            self.tc_table.removeRow(row + 1)
            self._expanded_row = None
            return

        if self._expanded_row is not None:
            self.tc_table.removeRow(self._expanded_row + 1)

        self._insert_image_row(row, images)
        self._expanded_row = row

    def _insert_image_row(self, row: int, images: list[str]):
        insert_at = row + 1
        self.tc_table.insertRow(insert_at)
        self.tc_table.setSpan(insert_at, 0, 1, self.tc_table.columnCount())

        container = QWidget()
        hl = QHBoxLayout(container)
        hl.setContentsMargins(10, 5, 10, 5)
        thumb_height = 120

        for b64 in images:
            pix_full = QPixmap()
            pix_full.loadFromData(base64.b64decode(b64))

            thumb = pix_full.scaledToHeight(thumb_height, Qt.SmoothTransformation)
            lbl = ClickableLabel()
            lbl.setPixmap(thumb)
            lbl._full_pixmap = pix_full

            lbl.clicked.connect(lambda pix=pix_full: self.show_full_image(pix))

            hl.addWidget(lbl)

        scroll = QScrollArea()
        scroll.setWidgetResizable(False)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(container)

        total_height = thumb_height + hl.contentsMargins().top() + hl.contentsMargins().bottom()
        scroll.setFixedHeight(total_height)
        self.tc_table.setCellWidget(insert_at, 0, scroll)
        self.tc_table.setRowHeight(insert_at, total_height)

    def show_full_image(self, pixmap: QPixmap):
        dlg = QDialog(self)
        dlg.setWindowTitle("Full-size Screenshot")
        vbox = QVBoxLayout(dlg)
        lbl = QLabel(dlg)
        lbl.setPixmap(pixmap)
        lbl.setAlignment(Qt.AlignCenter)

        scroll = QScrollArea(dlg)
        scroll.setWidget(lbl)
        scroll.setWidgetResizable(True)
        vbox.addWidget(scroll)

        dlg.resize(min(pixmap.width(), 1920), min(pixmap.height(), 1080))
        dlg.exec()
