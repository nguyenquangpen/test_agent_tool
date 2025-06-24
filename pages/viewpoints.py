import httpx
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpacerItem, QSizePolicy, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox
)
from pages.auths import access_token
from utils import base_url, fmt_datetime


class ViewpointsPage(QWidget):
    def __init__(self):
        super().__init__()
        self.viewpoints = []
        self._build_ui()
        self._get_viewpoints_data()
        self._populate()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("<h2>Viewpoints</h2>"))
        hdr.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum))
        add_btn = QPushButton("Add viewpoint")

        hdr.addWidget(add_btn)
        lay.addLayout(hdr)
        self.vp_table = QTableWidget(0, 6)
        self.vp_table.setHorizontalHeaderLabels([
            "Name",
            "Input description",
            "Expected error message",
            "Expected result",
            "Created at",
            "Last updated",
        ])
        hdr = self.vp_table.horizontalHeader()

        hdr.setSectionResizeMode(0, QHeaderView.Fixed)
        self.vp_table.setColumnWidth(0, 100)

        hdr.setSectionResizeMode(4, QHeaderView.Fixed)
        self.vp_table.setColumnWidth(4, 95)

        hdr.setSectionResizeMode(5, QHeaderView.Fixed)
        self.vp_table.setColumnWidth(5, 95)


        for col in (1, 2, 3):
            hdr.setSectionResizeMode(col, QHeaderView.Stretch)
        lay.addWidget(self.vp_table)

    def _get_viewpoints_data(self):
        try:
            with httpx.Client() as client:
                resp = client.get(
                    f"{base_url}/api/viewpoints",
                    headers={"Authorization": f"Bearer {access_token[-1]}"}
                )
            resp.raise_for_status()
            self.viewpoints = resp.json().get("data", [])
        except Exception as e:
            QMessageBox.critical(self, "Error loading viewpoints",
                                 f"Could not load viewpoints:\n{e}")
            self.viewpoints = []

    def _populate(self):
        self.vp_table.setRowCount(0)

        for vp in self.viewpoints:
            rows = vp.get("viewpoint_details", [])
            count = max(1, len(rows))
            start = self.vp_table.rowCount()

            for _ in rows:
                self.vp_table.insertRow(self.vp_table.rowCount())
            if not rows:
                self.vp_table.insertRow(self.vp_table.rowCount())

            self.vp_table.setSpan(start, 0, count, 1)
            self.vp_table.setSpan(start, 4, count, 1)
            self.vp_table.setSpan(start, 5, count, 1)

            self.vp_table.setItem(start, 0, QTableWidgetItem(vp.get("name", "")))
            created = vp.get("created_at", "")
            updated = vp.get("updated_at", "")
            self.vp_table.setItem(start, 4, QTableWidgetItem(fmt_datetime(created)))
            self.vp_table.setItem(start, 5, QTableWidgetItem(fmt_datetime(updated)))
            for i in range(count):
                row = start + i
                detail = rows[i] if i < len(rows) else {}
                self.vp_table.setItem(row, 1,
                    QTableWidgetItem(detail.get("description_input", "")))
                self.vp_table.setItem(row, 2,
                    QTableWidgetItem(detail.get("error_message_expected", "")))
                self.vp_table.setItem(row, 3,
                    QTableWidgetItem(detail.get("final_result_expected", "")))
