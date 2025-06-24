import httpx
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QSpacerItem, QSizePolicy, QMessageBox
)

from pages.auths import access_token
from utils import base_url, fmt_datetime


class ProjectsPage(QWidget):
    expand_project = Signal(dict)

    def __init__(self):
        super().__init__()

        lay = QVBoxLayout(self)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("<h2>Projects</h2>"))
        hdr.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum))
        hdr.addWidget(QPushButton("Add project"))
        lay.addLayout(hdr)

        self.proj_table = QTableWidget(0, 6)
        self.proj_table.setHorizontalHeaderLabels([
            "Name", "Description", "URL", "Created at", "Last updated", "Actions"
        ])
        hdr = self.proj_table.horizontalHeader()
        hdr.setSectionResizeMode(3, QHeaderView.Fixed)
        self.proj_table.setColumnWidth(3, 95)

        hdr.setSectionResizeMode(4, QHeaderView.Fixed)
        self.proj_table.setColumnWidth(4, 95)

        for col in (0, 1, 2):
            hdr.setSectionResizeMode(col, QHeaderView.Stretch)
        lay.addWidget(self.proj_table)

        self.projects = []

        self._get_projects_data()
        self._populate()

    def _get_projects_data(self):
        try:
            with httpx.Client() as client:
                resp = client.get(
                    f"{base_url}/api/projects",
                    headers={"Authorization": f"Bearer {access_token[-1]}"}
                )
            self.projects = resp.json().get("data", [])
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error loading test cases",
                f"Could not load projects data:\n{e}"
            )
            return

        pass

    def _populate(self):
        self.proj_table.setRowCount(0)
        for proj in self.projects:
            row = self.proj_table.rowCount()

            self.proj_table.insertRow(row)

            for col, key in enumerate(("name", "description", "url", "created_at", "updated_at")):
                raw = proj.get(key, "")

                if key in ("created_at", "updated_at"):
                    text = fmt_datetime(raw)
                else:
                    text = str(raw)
                self.proj_table.setItem(row, col, QTableWidgetItem(text))

            cell = QWidget()
            box = QHBoxLayout(cell)
            box.setContentsMargins(0,0,0,0)

            btn_expand = QPushButton("▶"); btn_expand.setFixedWidth(30)
            btn_expand.clicked.connect(lambda _, p=proj: self.expand_project.emit(p))
            btn_edit = QPushButton("✎"); btn_edit.setFixedWidth(30)
            btn_delete = QPushButton("❌"); btn_delete.setFixedWidth(30)
            for w in (btn_expand, btn_edit, btn_delete):
                box.addWidget(w)
            box.addStretch()
            self.proj_table.setCellWidget(row, 5, cell)
