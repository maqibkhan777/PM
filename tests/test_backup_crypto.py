"""Tests for backup cryptography, AES-256-GCM authenticated encryption, and SHA-256 checksumming."""

import os
import pytest
import tempfile
from pathlib import Path

from app.services.backup.crypto import (
    derive_key,
    calculate_sha256,
    verify_sha256,
    write_checksum_file,
    read_checksum_file,
    encrypt_file,
    decrypt_file,
    BackupCryptoError,
    DecryptionIntegrityError,
    InvalidBackupHeaderError,
    HEADER_MAGIC,
)


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


def test_derive_key_deterministic():
    """Verify that same secret and salt produce the exact same 32-byte derived key."""
    salt = os.urandom(16)
    key1 = derive_key("my-secret-passphrase", salt)
    key2 = derive_key("my-secret-passphrase", salt)
    key3 = derive_key("different-passphrase", salt)

    assert len(key1) == 32
    assert key1 == key2
    assert key1 != key3


def test_derive_key_empty_secret_raises():
    """Verify that empty secret raises BackupCryptoError."""
    with pytest.raises(BackupCryptoError, match="must not be empty"):
        derive_key("", os.urandom(16))


def test_calculate_and_verify_sha256(temp_dir):
    """Verify SHA-256 checksum calculation and constant-time verification."""
    test_file = temp_dir / "sample.data"
    test_file.write_bytes(b"Hello World PM Backup Data 12345")

    sha256_hash = calculate_sha256(test_file)
    assert len(sha256_hash) == 64
    assert verify_sha256(test_file, sha256_hash) is True
    assert verify_sha256(test_file, "0" * 64) is False
    assert verify_sha256(test_file, "") is False


def test_write_and_read_checksum_file(temp_dir):
    """Verify writing .sha256 files and reading them back correctly."""
    test_file = temp_dir / "archive.db.gz.enc"
    test_file.write_bytes(b"Simulated Encrypted Content")

    chk_path = write_checksum_file(test_file)
    assert chk_path.is_file()
    assert chk_path.name == "archive.db.gz.enc.sha256"

    parsed_hash = read_checksum_file(chk_path)
    expected_hash = calculate_sha256(test_file)
    assert parsed_hash == expected_hash


def test_aes_256_gcm_encrypt_decrypt_roundtrip(temp_dir):
    """Verify full AES-256-GCM authenticated encryption and decryption round-trip."""
    src_file = temp_dir / "database.db"
    enc_file = temp_dir / "database.db.gz.enc"
    dec_file = temp_dir / "database_restored.db"

    original_content = b"SQLite format 3\x00\x10\x00\x01\x01\x00@  \x00\x00\x00\x01" + (b"random data block" * 100)
    src_file.write_bytes(original_content)

    passphrase = "SuperSecureEncryptionPassphrase2026!"

    # Encrypt
    sha256_result = encrypt_file(src_file, enc_file, passphrase)
    assert enc_file.is_file()
    assert calculate_sha256(enc_file) == sha256_result

    # Verify header magic
    with open(enc_file, "rb") as f:
        magic = f.read(len(HEADER_MAGIC))
        assert magic == HEADER_MAGIC

    # Decrypt
    decrypt_file(enc_file, dec_file, passphrase)
    assert dec_file.is_file()
    assert dec_file.read_bytes() == original_content


def test_decrypt_with_wrong_key_fails(temp_dir):
    """Verify that attempting to decrypt with the wrong key raises DecryptionIntegrityError."""
    src_file = temp_dir / "plain.txt"
    enc_file = temp_dir / "plain.txt.enc"
    dec_file = temp_dir / "plain_out.txt"

    src_file.write_bytes(b"Confidential Project Management Data")
    encrypt_file(src_file, enc_file, "correct-key-12345")

    with pytest.raises(DecryptionIntegrityError, match="tag mismatch|corrupted|invalid key"):
        decrypt_file(enc_file, dec_file, "wrong-key-99999")


def test_decrypt_tampered_ciphertext_fails(temp_dir):
    """Verify that tampering with any bit of the ciphertext or tag fails authentication."""
    src_file = temp_dir / "plain.txt"
    enc_file = temp_dir / "plain.txt.enc"
    dec_file = temp_dir / "plain_out.txt"

    src_file.write_bytes(b"Authenticated Data Protection")
    encrypt_file(src_file, enc_file, "valid-key")

    # Read and tamper with ciphertext byte near the end
    raw_data = bytearray(enc_file.read_bytes())
    raw_data[-5] ^= 0xFF  # flip bits
    enc_file.write_bytes(raw_data)

    with pytest.raises(DecryptionIntegrityError):
        decrypt_file(enc_file, dec_file, "valid-key")


def test_decrypt_invalid_magic_header_fails(temp_dir):
    """Verify that files with invalid magic header are rejected immediately."""
    corrupt_file = temp_dir / "corrupt.enc"
    corrupt_file.write_bytes(b"BAD0" + os.urandom(100))

    with pytest.raises(InvalidBackupHeaderError, match="Invalid backup header magic"):
        decrypt_file(corrupt_file, temp_dir / "out.db", "any-key")


def test_decrypt_truncated_file_fails(temp_dir):
    """Verify that files smaller than minimal header size raise InvalidBackupHeaderError."""
    tiny_file = temp_dir / "tiny.enc"
    tiny_file.write_bytes(b"PME")

    with pytest.raises(InvalidBackupHeaderError, match="File too small"):
        decrypt_file(tiny_file, temp_dir / "out.db", "any-key")
