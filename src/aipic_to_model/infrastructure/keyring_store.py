from __future__ import annotations

import keyring
from keyring.errors import PasswordDeleteError

SERVICE_NAME = "Pic2Model Studio"


class OSKeyringStore:
    def set(self, profile: str, secret: str) -> None:
        keyring.set_password(SERVICE_NAME, profile, secret)

    def get(self, profile: str) -> str | None:
        return keyring.get_password(SERVICE_NAME, profile)

    def delete(self, profile: str) -> None:
        try:
            keyring.delete_password(SERVICE_NAME, profile)
        except PasswordDeleteError:
            return
