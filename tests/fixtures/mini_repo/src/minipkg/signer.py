"""A toy signer, shaped like itsdangerous.Signer."""

import hashlib
import hmac


class Signer:
    """Signs values with a secret key."""

    def __init__(self, secret_key: bytes) -> None:
        self.secret_key = secret_key

    def sign(self, value: bytes) -> bytes:
        return value + b"." + self.signature(value)

    def signature(self, value: bytes) -> bytes:
        return hmac.new(self.secret_key, value, hashlib.sha256).hexdigest().encode()


def verify(signer: Signer, signed: bytes) -> bool:
    value, _, sig = signed.rpartition(b".")
    return hmac.compare_digest(sig, signer.signature(value))
