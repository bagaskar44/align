from __future__ import annotations

import hashlib
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def _write(path: Path, contents: bytes) -> None:
    path.write_bytes(contents)
    try:
        os.chmod(path, 0o600 if path.suffix == ".key" else 0o644)
    except OSError:
        pass


def create(private_dir: Path) -> None:
    paths = {
        "root_cert": private_dir / "ca.crt",
        "root_key": private_dir / "ca.key",
        "leaf_cert": private_dir / "align-b9.crt",
        "leaf_key": private_dir / "align-b9.key",
        "thumbprint": private_dir / "ca-thumbprint.txt",
    }
    existing = [path.exists() for path in paths.values()]
    if all(existing):
        return
    if any(existing):
        raise SystemExit("B9 certificate files are incomplete; restore the ignored private files before setup.")

    now = datetime.now(UTC)
    root_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ALIGN B9 Local Sandbox Root")])
    root_cert = (
        x509.CertificateBuilder()
        .subject_name(root_name)
        .issuer_name(root_name)
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1095))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(root_key.public_key()), critical=False)
        .sign(root_key, hashes.SHA256())
    )

    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "align-b9.localhost")])
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(leaf_name)
        .issuer_name(root_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("align-b9.localhost"),
                    x509.DNSName("moodle-b9.localhost"),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(root_key, hashes.SHA256())
    )
    _write(
        paths["root_cert"],
        root_cert.public_bytes(serialization.Encoding.PEM),
    )
    _write(
        paths["root_key"],
        root_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    _write(
        paths["leaf_cert"],
        leaf_cert.public_bytes(serialization.Encoding.PEM),
    )
    _write(
        paths["leaf_key"],
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    paths["thumbprint"].write_text(
        hashlib.sha1(root_cert.public_bytes(serialization.Encoding.DER), usedforsecurity=False)
        .hexdigest()
        .upper(),
        encoding="ascii",
    )


if __name__ == "__main__":
    create(Path(sys.argv[1]))
