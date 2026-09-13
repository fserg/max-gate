from cryptography.fernet import Fernet
from pydantic import SecretStr


class Crypto:
    def __init__(self, key: str | SecretStr):
        self._fernet = Fernet(key.get_secret_value() if isinstance(key, SecretStr) else key)

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode("ascii")

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode("ascii")

    def decrypt(self, value: str) -> str:
        return self._fernet.decrypt(value.encode("ascii")).decode()
