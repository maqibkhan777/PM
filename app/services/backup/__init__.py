"""SQLite backup and recovery package."""

from app.services.backup.crypto import (
    encrypt_file,
    decrypt_file,
    calculate_sha256,
    verify_sha256,
    write_checksum_file,
    read_checksum_file,
    BackupCryptoError,
    DecryptionIntegrityError,
    InvalidBackupHeaderError,
)
from app.services.backup.gdrive import (
    GoogleDriveClient,
    GoogleDriveError,
    GoogleDriveConfigError,
    GoogleDriveVerificationError,
)
from app.services.backup.manager import (
    BackupManager,
    backup_manager,
    BackupExecutionError,
)
from app.services.backup.restore import (
    RestoreManager,
    restore_manager,
    RestoreError,
    RestoreIntegrityError,
    RestoreSchemaError,
)

__all__ = [
    "encrypt_file",
    "decrypt_file",
    "calculate_sha256",
    "verify_sha256",
    "write_checksum_file",
    "read_checksum_file",
    "BackupCryptoError",
    "DecryptionIntegrityError",
    "InvalidBackupHeaderError",
    "GoogleDriveClient",
    "GoogleDriveError",
    "GoogleDriveConfigError",
    "GoogleDriveVerificationError",
    "BackupManager",
    "backup_manager",
    "BackupExecutionError",
    "RestoreManager",
    "restore_manager",
    "RestoreError",
    "RestoreIntegrityError",
    "RestoreSchemaError",
]
