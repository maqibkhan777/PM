#!/usr/bin/env python3
"""PM Operations Agent — Google Drive & Local Backup CLI Utility.

Executes SQLite hot backup, integrity checking, gzip compression, AES-256-GCM encryption,
SHA-256 checksumming, local retention pruning, and atomic Google Drive cloud sync.
"""

import sys
import argparse
import logging
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config.settings import settings
from app.services.backup.manager import BackupManager


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main():
    parser = argparse.ArgumentParser(
        description="Execute automated SQLite backup, encryption, and Google Drive upload."
    )
    parser.add_argument(
        "--db-path",
        default=settings.DB_PATH,
        help="Path to the active SQLite database file (default: %(default)s)",
    )
    parser.add_argument(
        "--backup-dir",
        default=settings.BACKUP_DIR,
        help="Directory to store local backup archives (default: %(default)s)",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Perform only local backup and encryption, skipping Google Drive upload",
    )
    parser.add_argument(
        "--encryption-key",
        default=settings.BACKUP_ENCRYPTION_KEY,
        help="Encryption passphrase/key for AES-256-GCM (default: from BACKUP_ENCRYPTION_KEY env)",
    )
    parser.add_argument(
        "--gdrive-folder-id",
        default=settings.GDRIVE_FOLDER_ID,
        help="Google Drive target folder ID (default: from GDRIVE_FOLDER_ID env)",
    )
    parser.add_argument(
        "--gdrive-sa-file",
        default=settings.GDRIVE_SERVICE_ACCOUNT_FILE,
        help="Path to Google Cloud Service Account JSON file (default: from GDRIVE_SERVICE_ACCOUNT_FILE env)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose debug logging",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    gdrive_enabled = False if args.local_only else settings.GDRIVE_ENABLED

    manager = BackupManager(
        db_path=args.db_path,
        backup_dir=args.backup_dir,
        encryption_enabled=settings.BACKUP_ENCRYPTION_ENABLED,
        encryption_key=args.encryption_key,
        gdrive_enabled=gdrive_enabled,
        gdrive_folder_id=args.gdrive_folder_id,
        gdrive_service_account_file=args.gdrive_sa_file,
    )

    print("=================================================================")
    print("PM Operations Agent — Automated Hot Backup Pipeline")
    print("=================================================================")
    print(f"Source Database  : {args.db_path}")
    print(f"Backup Directory : {args.backup_dir}")
    print(f"Encryption       : {'ENABLED' if settings.BACKUP_ENCRYPTION_ENABLED and args.encryption_key else 'DISABLED/NO KEY'}")
    print(f"Google Drive     : {'ENABLED' if gdrive_enabled else 'DISABLED/LOCAL ONLY'}")
    print("-----------------------------------------------------------------")

    try:
        result = manager.run_backup_pipeline()
        print(f"[SUCCESS] Backup created successfully!")
        print(f"Backup ID          : {result['backup_id']}")
        print(f"Local Artifact     : {result['local_path']}")
        print(f"Artifact Size      : {result['encrypted_size']} bytes")
        print(f"SHA-256 Checksum   : {result['sha256']}")
        print(f"Upload Status      : {result['upload_status']}")
        print(f"Verification Status: {result['verification_status']}")
        if result.get("remote_file_id"):
            print(f"Remote File ID     : {result['remote_file_id']}")
        if result.get("error_summary"):
            print(f"[WARNING] Remote Sync Warning: {result['error_summary']}")
        print("=================================================================")
        return 0
    except Exception as e:
        print(f"[ERROR] Backup pipeline failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
