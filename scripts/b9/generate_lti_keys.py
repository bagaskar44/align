from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def _b64url(value: int) -> str:
    width = (value.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(value.to_bytes(width, "big")).rstrip(b"=").decode("ascii")


def _public_document(key: rsa.RSAPrivateKey) -> dict[str, object]:
    numbers = key.public_key().public_numbers()
    modulus = _b64url(numbers.n)
    exponent = _b64url(numbers.e)
    kid = hashlib.sha256(f"{modulus}.{exponent}".encode("ascii")).hexdigest()[:32]
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "key_ops": ["verify"],
                "kid": kid,
                "n": modulus,
                "e": exponent,
            }
        ]
    }


def _write_atomic(path: Path, value: bytes, mode: int = 0o600) -> None:
    temporary = path.with_suffix(path.suffix + ".next")
    temporary.write_bytes(value)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: generate_lti_keys.py PRIVATE_DIRECTORY")
    private_dir = Path(sys.argv[1]).resolve()
    private_dir.mkdir(parents=True, exist_ok=True)
    private_path = private_dir / "lti-tool-private.pem"
    jwks_path = private_dir / "lti-tool-jwks.json"
    present = (private_path.exists(), jwks_path.exists())
    if present == (False, False):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        jwks_bytes = (json.dumps(_public_document(private_key), sort_keys=True) + "\n").encode()
        _write_atomic(private_path, private_bytes)
        _write_atomic(jwks_path, jwks_bytes, mode=0o644)
    elif present == (True, True):
        private_key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
        if not isinstance(private_key, rsa.RSAPrivateKey):
            raise SystemExit("existing LTI tool key is not RSA")
        expected = _public_document(private_key)
        try:
            existing = json.loads(jwks_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit("existing LTI tool JWKS is invalid") from exc
        if existing != expected:
            legacy_key = dict(expected["keys"][0])
            legacy_key.pop("key_ops")
            if existing == {"keys": [legacy_key]}:
                _write_atomic(
                    jwks_path,
                    (json.dumps(expected, sort_keys=True) + "\n").encode(),
                    mode=0o644,
                )
            else:
                raise SystemExit("existing LTI private key and public JWKS do not match")
    else:
        raise SystemExit("incomplete LTI tool key pair; restore both files or rotate explicitly")
    print("B9 LTI public keyset created or verified; private key remains in the ignored private directory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
