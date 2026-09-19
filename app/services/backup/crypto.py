"""Cryptographic utilities for authenticated SQLite backup encryption and verification.

Implements AES-256-GCM authenticated encryption with PBKDF2HMAC key derivation
and SHA-256 integrity checksumming.
"""

import os
import hmac
import hashlib
from pathlib import Path
from typing import Union, Optional
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

# File Header Magic for PM Encrypted Backup v1
HEADER_MAGIC = b"PME1"
SALT_SIZE = 16
NONCE_SIZE = 12  # Standard 96-bit nonce for AES-GCM
PBKDF2_ITERATIONS = 100_000
CHUNK_SIZE = 64 * 1024  # 64 KB for streaming operations


class BackupCryptoError(Exception):
    """Base exception for backup encryption/decryption operations."""
    pass


class DecryptionIntegrityError(BackupCryptoError):
    """Raised when backup ciphertext has been tampered with or tag verification fails."""
    pass


class InvalidBackupHeaderError(BackupCryptoError):
    """Raised when encrypted backup file does not have a valid PME1 magic header."""
    pass


def derive_key(secret: Union[str, bytes], salt: bytes) -> bytes:
    """Derive a 256-bit AES key from a passphrase/key and salt using PBKDF2-HMAC-SHA256."""
    if not secret:
        raise BackupCryptoError("Encryption secret/key must not be empty.")

    secret_bytes = secret.encode("utf-8") if isinstance(secret, str) else secret

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(secret_bytes)


def calculate_sha256(file_path: Union[str, Path]) -> str:
    """Calculate the SHA-256 hexadecimal checksum of a file by streaming."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found for checksum calculation: {file_path}")

    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_sha256(file_path: Union[str, Path], expected_checksum: str) -> bool:
    """Verify that a file's SHA-256 checksum matches the expected hash using constant-time comparison."""
    if not expected_checksum:
        return False
    actual_checksum = calculate_sha256(file_path)
    return hmac.compare_digest(actual_checksum.strip().lower(), expected_checksum.strip().lower())


def write_checksum_file(target_file: Union[str, Path], checksum_output_path: Optional[Union[str, Path]] = None) -> Path:
    """Compute SHA-256 of target_file and write to .sha256 file."""
    target_path = Path(target_file)
    if not target_path.is_file():
        raise FileNotFoundError(f"Target file for checksum does not exist: {target_file}")

    if checksum_output_path is None:
        checksum_path = target_path.with_name(f"{target_path.name}.sha256")
    else:
        checksum_path = Path(checksum_output_path)

    sha256_hash = calculate_sha256(target_path)
    checksum_content = f"{sha256_hash}  {target_path.name}\n"

    with open(checksum_path, "w", encoding="utf-8") as f:
        f.write(checksum_content)

    return checksum_path


def read_checksum_file(checksum_file_path: Union[str, Path]) -> str:
    """Read and parse the SHA-256 hash from a checksum file."""
    path = Path(checksum_file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Checksum file not found: {checksum_file_path}")

    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()

    if not content:
        raise BackupCryptoError(f"Checksum file is empty: {checksum_file_path}")

    # Handle formats: '<hash>' or '<hash>  <filename>' or '<hash> *<filename>'
    parts = content.split()
    return parts[0].lower()


def encrypt_file(
    src_file: Union[str, Path],
    dst_file: Union[str, Path],
    secret_key: Union[str, bytes],
) -> str:
    """Encrypt a file using AES-256-GCM authenticated encryption.
    
    Layout of output:
    [4 bytes MAGIC (b"PME1")] + [16 bytes SALT] + [12 bytes NONCE] + [CIPHERTEXT + 16 bytes TAG]
    
    Returns the SHA-256 checksum of the resulting encrypted file.
    """
    src_path = Path(src_file)
    dst_path = Path(dst_file)

    if not src_path.is_file():
        raise FileNotFoundError(f"Source file to encrypt does not exist: {src_file}")

    if not secret_key:
        raise BackupCryptoError("Cannot encrypt backup without a valid encryption key.")

    salt = os.urandom(SALT_SIZE)
    nonce = os.urandom(NONCE_SIZE)
    derived_key = derive_key(secret_key, salt)
    aesgcm = AESGCM(derived_key)

    with open(src_path, "rb") as f:
        plaintext = f.read()

    # Encrypt with header magic as associated data (AAD) for authenticated binding
    ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext, associated_data=HEADER_MAGIC)

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dst_path, "wb") as f:
        f.write(HEADER_MAGIC)
        f.write(salt)
        f.write(nonce)
        f.write(ciphertext_with_tag)

    return calculate_sha256(dst_path)


def decrypt_file(
    src_file: Union[str, Path],
    dst_file: Union[str, Path],
    secret_key: Union[str, bytes],
) -> Path:
    """Decrypt an AES-256-GCM encrypted backup file.
    
    Validates magic header, derives key with embedded salt, and verifies GCM authentication tag.
    Raises DecryptionIntegrityError if ciphertext or header has been tampered with or key is incorrect.
    """
    src_path = Path(src_file)
    dst_path = Path(dst_file)

    if not src_path.is_file():
        raise FileNotFoundError(f"Encrypted source file not found: {src_file}")

    if not secret_key:
        raise BackupCryptoError("Cannot decrypt backup without a valid encryption key.")

    file_size = src_path.stat().st_size
    min_size = len(HEADER_MAGIC) + SALT_SIZE + NONCE_SIZE + 16  # minimum size with 16-byte GCM tag
    if file_size < min_size:
        raise InvalidBackupHeaderError(
            f"File too small ({file_size} bytes) to be a valid PM encrypted backup."
        )

    with open(src_path, "rb") as f:
        magic = f.read(len(HEADER_MAGIC))
        if magic != HEADER_MAGIC:
            raise InvalidBackupHeaderError(
                f"Invalid backup header magic {magic!r}. Expected {HEADER_MAGIC!r}."
            )

        salt = f.read(SALT_SIZE)
        nonce = f.read(NONCE_SIZE)
        ciphertext_with_tag = f.read()

    derived_key = derive_key(secret_key, salt)
    aesgcm = AESGCM(derived_key)

    try:
        plaintext = aesgcm.decrypt(nonce, ciphertext_with_tag, associated_data=HEADER_MAGIC)
    except Exception as e:
        raise DecryptionIntegrityError(
            "Backup decryption failed. Verification tag mismatch, ciphertext corrupted, or invalid key."
        ) from e

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dst_path, "wb") as f:
        f.write(plaintext)

    return dst_path
