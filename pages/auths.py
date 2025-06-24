import asyncio

import httpx
from qasync import asyncSlot
from PySide6.QtWidgets import QPushButton, QDialog, QFormLayout, QLineEdit, QMessageBox
from utils import base_url

access_token = []

class LoginDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Login")
        self.setModal(True)
        self.resize(300, 120)

        layout = QFormLayout(self)
        self.user_edit = QLineEdit()
        self.pw_edit   = QLineEdit()
        self.pw_edit.setEchoMode(QLineEdit.Password)
        layout.addRow("Username:", self.user_edit)
        layout.addRow("Password:", self.pw_edit)

        btn = QPushButton("Log In")
        btn.clicked.connect(self.attempt_login)
        layout.addRow(btn)

    @property
    def get_access_token(self):
        print(self.access_token)
        return str(self.access_token)

    async def exec_async(self) -> int:
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self.finished.connect(fut.set_result)
        self.open()
        return await fut

    @asyncSlot()
    async def attempt_login(self):
        user = self.user_edit.text().strip()
        pw   = self.pw_edit.text()

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{base_url}/auth/login",
                    json={"username": user, "password": pw},
                )
        except httpx.TransportError:
            QMessageBox.critical(self, "Network Error", "Could not reach authentication server.")
            return

        if resp.status_code == 200:
            data = resp.json()
            token = data.get("access_token")
            if token:
                access_token.append(token)
                self.accept()
                return

        QMessageBox.warning(self, "Login Failed", "Invalid credentials")
