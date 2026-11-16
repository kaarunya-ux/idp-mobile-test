"""Edit-plan passwords: decrypted with the service's key; no key, or a bad token, is a clear CNV-422-PLAN-001."""

import base64

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from conversion.conversion.domain.exceptions import InvalidPlanError
from conversion.conversion.infrastructure.secrets import RsaSecrets

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
OAEP = padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)


def _encrypt(password: str) -> str:
    return base64.b64encode(KEY.public_key().encrypt(password.encode(), OAEP)).decode()


def test_returns_the_password_when_java_encrypted_it_with_our_public_key() -> None:
    assert RsaSecrets(PEM).decrypt(_encrypt("pässwörd")) == "pässwörd"


@pytest.mark.parametrize(
    ("key", "token", "message"),
    [
        (None, "x", "no CRYPTO__PRIVATE_KEY"),
        (PEM, "not base64!", "can't be decrypted"),
        (PEM, base64.b64encode(b"junk").decode(), "can't be decrypted"),
    ],
)
def test_reports_cnv_422_plan_001_when_the_password_cannot_be_read(key: str | None, token: str, message: str) -> None:
    with pytest.raises(InvalidPlanError, match=message):
        RsaSecrets(key).decrypt(token)
