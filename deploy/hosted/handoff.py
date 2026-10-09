"""Secret handoff between independently hosted machines over an untrusted channel.

The hosted end-to-end run passes a per-worker credential from the control host to the worker
host through CI artifacts, which other readers of the repository can also see. The secret is
therefore sealed to a key that only the worker host holds:

    worker:  python handoff.py keygen  key.json  pub.txt     # X25519 keypair; pub is public
    control: python handoff.py seal    pub.txt   secret.txt  sealed.json
    worker:  python handoff.py open    key.json  sealed.json secret.txt

Sealing: ephemeral X25519 + HKDF-SHA256 + AES-256-GCM (the `cryptography` package). The sealed
blob is useless without the worker host's private key, which never leaves that host.
"""

from __future__ import annotations

import base64
import json
import os
import sys

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

INFO = b"daedelus-hosted-handoff-v1"


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _raw(pub) -> bytes:
    return pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _key(shared: bytes, salt: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=INFO).derive(shared)


def keygen(key_path: str, pub_path: str) -> None:
    k = X25519PrivateKey.generate()
    raw = k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                          serialization.NoEncryption())
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"x25519": _b64(raw)}, f)
    with open(pub_path, "w") as f:
        f.write(_b64(_raw(k.public_key())))


def seal(pub_path: str, secret_path: str, out_path: str) -> None:
    recipient = X25519PublicKey.from_public_bytes(base64.b64decode(open(pub_path).read()))
    eph = X25519PrivateKey.generate()
    eph_pub = _raw(eph.public_key())
    key = _key(eph.exchange(recipient), eph_pub)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, open(secret_path, "rb").read(), INFO)
    with open(out_path, "w") as f:
        json.dump({"v": 1, "eph": _b64(eph_pub), "nonce": _b64(nonce), "ct": _b64(ct)}, f)


def open_(key_path: str, sealed_path: str, out_path: str) -> None:
    k = X25519PrivateKey.from_private_bytes(base64.b64decode(json.load(open(key_path))["x25519"]))
    s = json.load(open(sealed_path))
    eph = base64.b64decode(s["eph"])
    key = _key(k.exchange(X25519PublicKey.from_public_bytes(eph)), eph)
    pt = AESGCM(key).decrypt(base64.b64decode(s["nonce"]), base64.b64decode(s["ct"]), INFO)
    fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pt)


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    {"keygen": keygen, "seal": seal, "open": open_}[cmd](*args)
