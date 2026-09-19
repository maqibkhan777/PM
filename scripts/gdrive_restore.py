#!/usr/bin/env python3
"""PM Operations Agent — Google Drive Restore & Verification Utility.

Locates canonical remote backup on Google Drive, downloads to an isolated temporary
environment, verifies SHA-256 checksum, decrypts, decompresses, and runs deep SQLite
integrity & schema validation.

Default mode is strictly DOWNLOAD + VERIFY ONLY without touching production.
"""

import os
import sys
import argparse
import tempfile
import logging
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config.settings import settings
from app.services.backup.gdrive import GoogleDriveClient, DEFAULT_BACKUP_FILENAME, DEFAULT_CHECKSUM_FILENAME
from app.services.backup.restore import RestoreManager, RestoreIntegrityError, RestoreSchemaError


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main():
    parser = argparse.ArgumentParser(
        description="Download, verify, and optionally restore Google Drive SQLite backup."
    )
    parser.add_argument(
        "--output-db",
        default=None,
        help="Target path to write the verified decompressed SQLite .db (default: safe temp directory)",
    )
    parser.add_argument(
        "--encryption-key",
        default=settings.BACKUP_ENCRYPTION_KEY,
        help="Encryption passphrase/key for AES-256-GCM decryption (default: from BACKUP_ENCRYPTION_KEY env)",
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
        "--backup-filename",
        default=DEFAULT_BACKUP_FILENAME,
        help="Remote canonical backup filename to locate (default: %(default)s)",
    )
    parser.add_argument(
        "--checksum-filename",
        default=DEFAULT_CHECKSUM_FILENAME,
        help="Remote canonical checksum filename to locate (default: %(default)s)",
    )
    parser.add_argument(
        "--apply-to-production",
        action="store_true",
        help="DANGER: Apply restored database to active production database after validation",
    )
    parser.add_argument(
        "--confirm-production-swap",
        action="store_true",
        help="Confirmation flag required when --apply-to-production is passed",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose debug logging",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    print("=================================================================")
    print("PM Operations Agent — Google Drive Recovery & Verification")
    print("=================================================================")
    print(f"Target Remote Backup : {args.backup_filename}")
    print(f"Target Checksum File : {args.checksum_filename}")
    print(f"Service Account File : {args.gdrive_sa_file}")
    print(f"Mode                 : {'PRODUCTION RESTORE' if args.apply_to_production else 'DOWNLOAD + VERIFY ONLY (SAFE)'}")
    print("-----------------------------------------------------------------")

    gdrive_client = GoogleDriveClient(
        service_account_file=args.gdrive_sa_file,
        folder_id=args.gdrive_folder_id,
        enabled=True,
    )

    if not gdrive_client.is_configured():
        print("[ERROR] Google Drive is not configured properly. Check folder ID and service account file.", file=sys.stderr)
        return 1

    temp_dir = tempfile.mkdtemp(prefix="pm_gdrive_restore_")
    try:
        # 1. Locate remote backup and checksum files
        print("[1/5] Searching for canonical backup on Google Drive...")
        backups = gdrive_client.find_files(args.backup_filename)
        if not backups:
            print(f"[ERROR] No remote backup named '{args.backup_filename}' found in Google Drive folder.", file=sys.stderr)
            return 1
        remote_backup = backups[0]
        print(f"      Found remote backup: ID={remote_backup['id']}, Size={remote_backup.get('size')} bytes")

        checksums = gdrive_client.find_files(args.checksum_filename)
        remote_checksum = checksums[0] if checksums else None
        if remote_checksum:
            print(f"      Found remote checksum: ID={remote_checksum['id']}")
        else:
            print(f"      [WARNING] No remote checksum file '{args.checksum_filename}' found.")

        # 2. Download remote artifacts to isolated temp directory
        print("[2/5] Downloading remote artifacts...")
        downloaded_backup = Path(temp_dir) / args.backup_filename
        gdrive_client.download_file(remote_backup["id"], downloaded_backup)
        print(f"      Downloaded backup artifact to {downloaded_backup}")

        downloaded_checksum = None
        if remote_checksum:
            downloaded_checksum = Path(temp_dir) / args.checksum_filename
            gdrive_client.download_file(remote_checksum["id"], downloaded_checksum)
            print(f"      Downloaded checksum artifact to {downloaded_checksum}")

        # 3. Decrypt, decompress, and verify
        print("[3/5] Decrypting, decompressing, and verifying SQLite integrity...")
        restore_mgr = RestoreManager(default_encryption_key=args.encryption_key)
        
        output_db_path = args.output_db or (Path(temp_dir) / "verified_pm_operations.db")
        extracted_db, metadata = restore_mgr.extract_and_verify(
            backup_file_path=downloaded_backup,
            checksum_file_path=downloaded_checksum,
            encryption_key=args.encryption_key,
            target_output_db=output_db_path,
        )

        print("[4/5] Verification Results:")
        print(f"      - SHA-256 Checksum Verified : {metadata['checksum_verified']}")
        print(f"      - Decryption Status         : {metadata['decrypted']}")
        print(f"      - Decompression Status      : {metadata['decompressed']}")
        print(f"      - SQLite PRAGMA Integrity   : {metadata['sqlite_integrity']}")
        print(f"      - Foreign Key Integrity     : {metadata['foreign_key_integrity']}")
        print(f"      - Schema Verification       : {metadata['schema_verified']}")
        print(f"      - Phase A Smoke Analysis    : {metadata['smoke_phase_a']}")
        print(f"      - Phase B Smoke Analysis    : {metadata['smoke_phase_b']}")
        print(f"      - Verification Duration     : {metadata['duration_seconds']}s")
        print(f"      - Output Verified DB Path   : {extracted_db}")

        # 4. Optional Production Restore Gate
        if args.apply_to_production:
            if not args.confirm_production_swap:
                print("\n[ERROR] Production restore requested without --confirm-production-swap. Aborting.", file=sys.stderr)
                return 1
            
            print("\n[5/5] Executing Safe Production Database Swap...")
            prod_db = settings.get_database_path()
            prod_meta = restore_mgr.safe_production_restore(
                backup_file_path=downloaded_backup,
                target_production_db=prod_db,
                encryption_key=args.encryption_key,
            )
            print(f"[SUCCESS] Production database safely updated! Rollback snapshot: {prod_meta.get('rollback_snapshot_path')}")
        else:
            print("\n[5/5] Safe verification complete. Production database was NOT modified.")

        print("=================================================================")
        return 0

    except Exception as e:
        print(f"\n[ERROR] Restore & Verification failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
