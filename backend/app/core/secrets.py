"""OS-backed credentials. Fail closed rather than silently writing plaintext secrets."""
import importlib.util
import json
from cryptography.fernet import Fernet

SERVICE='ClipRank'


class SecretVault:
    @staticmethod
    def ready():
        if not importlib.util.find_spec('keyring'): return False
        import keyring
        return keyring.get_keyring().priority>0

    @staticmethod
    def get(name):
        if not SecretVault.ready(): raise ValueError('Secure OS credential storage is unavailable. Install keyring and enable Keychain/OS secret storage.')
        import keyring
        return keyring.get_password(SERVICE,name)

    @staticmethod
    def set(name,value):
        if not SecretVault.ready(): raise ValueError('Secure OS credential storage is unavailable. Install keyring and enable Keychain/OS secret storage.')
        import keyring
        keyring.set_password(SERVICE,name,value)

    @classmethod
    def encryption_key(cls):
        key=cls.get('local-encryption-key')
        if not key:
            key=Fernet.generate_key().decode(); cls.set('local-encryption-key',key)
        return key.encode()

    @classmethod
    def encrypt(cls,data):
        return {'version':1,'ciphertext':Fernet(cls.encryption_key()).encrypt(json.dumps(data).encode()).decode()}

    @classmethod
    def decrypt(cls,data):
        return json.loads(Fernet(cls.encryption_key()).decrypt(data['ciphertext'].encode()))
