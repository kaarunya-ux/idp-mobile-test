"""Passwords in edit plans, encrypted by Java with this service's public key (IDP-03 ch. 3.5 and 3.7).

Format (to agree with the Java team): base64 of RSA-OAEP with SHA-256, encrypting the UTF-8 password. The private
key comes from ``CRYPTO__PRIVATE_KEY`` (PEM). Plaintext never leaves the worker's memory and is never logged.
"""

import base64
import binascii

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from conversion.conversion.domain.exceptions import InvalidPlanError

_OAEP = padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)


class RsaSecrets:
    def __init__(self, private_key_pem: str | None) -> None:
        self._key: rsa.RSAPrivateKey | None = None
        if private_key_pem:
            key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
            if not isinstance(key, rsa.RSAPrivateKey):
                raise ValueError("CRYPTO__PRIVATE_KEY must be an RSA private key")  # a deployment mistake
            self._key = key

    def decrypt(self, token: str) -> str:
        if self._key is None:
            raise InvalidPlanError("This service can't read encrypted passwords yet (no CRYPTO__PRIVATE_KEY).")
        try:
            return self._key.decrypt(base64.b64decode(token, validate=True), _OAEP).decode("utf-8")
        except (ValueError, binascii.Error, UnicodeDecodeError):
            raise InvalidPlanError("A password in the edit plan can't be decrypted.") from None
