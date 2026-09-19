"""Google Drive backup integration with authenticated Service Account support,
staging uploads, remote checksum verification, and atomic single-current-backup replacement.
"""

import os
import io
import uuid
import logging
import tempfile
from pathlib import Path
from typing import Dict, Any, List, Optional, Union
from app.services.backup.crypto import calculate_sha256, verify_sha256

logger = logging.getLogger(__name__)

# Google Drive API Constants
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
DEFAULT_BACKUP_FILENAME = "PM_Operations_Latest.db.gz.enc"
DEFAULT_CHECKSUM_FILENAME = "PM_Operations_Latest.db.gz.enc.sha256"


class GoogleDriveError(Exception):
    """Base exception for Google Drive backup operations."""
    pass


class GoogleDriveConfigError(GoogleDriveError):
    """Raised when Google Drive is misconfigured or credentials are missing."""
    pass


class GoogleDriveVerificationError(GoogleDriveError):
    """Raised when remote artifact verification (size or SHA-256) fails."""
    pass


def sanitize_error(error_msg: str) -> str:
    """Sanitize error messages to ensure no tokens, keys, or internal secrets leak."""
    redacted = str(error_msg)
    # Redact potential private keys or authorization headers
    for secret_pattern in ["BEGIN PRIVATE KEY", "Bearer ", "private_key"]:
        if secret_pattern in redacted:
            return "Google Drive API error occurred (sensitive details redacted)"
    return redacted


class GoogleDriveClient:
    """Manages Google Drive uploads, verifications, downloads, and single-current-backup lifecycle."""

    def __init__(
        self,
        service_account_file: Optional[str] = None,
        folder_id: Optional[str] = None,
        enabled: bool = False,
    ):
        self.service_account_file = service_account_file
        self.folder_id = folder_id
        self.enabled = enabled
        self._service = None

    def is_configured(self) -> bool:
        """Check if Google Drive integration is properly configured."""
        if not self.enabled:
            return False
        if not self.folder_id or not self.folder_id.strip():
            return False
        if not self.service_account_file or not os.path.isfile(self.service_account_file):
            return False
        return True

    def _get_drive_service(self):
        """Lazily initialize and return the Google Drive API v3 resource client."""
        if self._service is not None:
            return self._service

        if not self.is_configured():
            raise GoogleDriveConfigError(
                "Google Drive is not configured. Check GDRIVE_ENABLED, GDRIVE_FOLDER_ID, and GDRIVE_SERVICE_ACCOUNT_FILE."
            )

        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            credentials = service_account.Credentials.from_service_account_file(
                self.service_account_file,
                scopes=DRIVE_SCOPES,
            )
            self._service = build("drive", "v3", credentials=credentials, cache_discovery=False)
            return self._service
        except ImportError as e:
            raise GoogleDriveConfigError(
                f"Google Drive client libraries not installed: {e}. Ensure google-api-python-client and google-auth are installed."
            )
        except Exception as e:
            sanitized = sanitize_error(str(e))
            logger.error(f"Failed to authenticate with Google Drive service account: {sanitized}")
            raise GoogleDriveError(f"Authentication failed: {sanitized}") from e

    def find_files(self, filename: str, folder_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List active (non-trashed) files matching the exact filename in the specified folder."""
        service = self._get_drive_service()
        target_folder = folder_id or self.folder_id

        # Query for non-trashed files with exact name inside target folder
        escaped_name = filename.replace("'", "\\'")
        query = f"name = '{escaped_name}' and trashed = false"
        if target_folder:
            query += f" and '{target_folder}' in parents"

        try:
            response = service.files().list(
                q=query,
                spaces="drive",
                fields="files(id, name, size, md5Checksum, modifiedTime, createdTime)",
            ).execute()
            return response.get("files", [])
        except Exception as e:
            sanitized = sanitize_error(str(e))
            logger.error(f"Error querying Google Drive files ({filename}): {sanitized}")
            raise GoogleDriveError(f"Search failed for {filename}: {sanitized}") from e

    def upload_file(
        self,
        local_path: Union[str, Path],
        remote_name: str,
        folder_id: Optional[str] = None,
        mime_type: str = "application/octet-stream",
    ) -> Dict[str, Any]:
        """Upload a file to Google Drive. Returns file metadata dictionary including 'id'."""
        service = self._get_drive_service()
        local_file = Path(local_path)
        target_folder = folder_id or self.folder_id

        if not local_file.is_file():
            raise FileNotFoundError(f"Local file to upload does not exist: {local_path}")

        try:
            from googleapiclient.http import MediaFileUpload

            file_metadata = {
                "name": remote_name,
            }
            if target_folder:
                file_metadata["parents"] = [target_folder]

            media = MediaFileUpload(
                str(local_file),
                mimetype=mime_type,
                resumable=True,
            )

            uploaded_file = service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id, name, size, md5Checksum, createdTime",
            ).execute()

            logger.info(f"Successfully uploaded {local_file.name} to Google Drive as '{remote_name}' (ID: {uploaded_file.get('id')})")
            return uploaded_file
        except Exception as e:
            sanitized = sanitize_error(str(e))
            logger.error(f"Failed to upload {local_file.name} to Google Drive: {sanitized}")
            raise GoogleDriveError(f"Upload failed for {remote_name}: {sanitized}") from e

    def download_file(self, file_id: str, local_dst_path: Union[str, Path]) -> Path:
        """Download a file by file_id from Google Drive to local_dst_path."""
        service = self._get_drive_service()
        dst_path = Path(local_dst_path)
        dst_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            from googleapiclient.http import MediaIoBaseDownload

            request = service.files().get_media(fileId=file_id)
            with open(dst_path, "wb") as f:
                downloader = MediaIoBaseDownload(f, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()

            logger.info(f"Successfully downloaded Google Drive file {file_id} to {dst_path}")
            return dst_path
        except Exception as e:
            sanitized = sanitize_error(str(e))
            logger.error(f"Failed to download Google Drive file {file_id}: {sanitized}")
            raise GoogleDriveError(f"Download failed for {file_id}: {sanitized}") from e

    def get_file_metadata(self, file_id: str) -> Dict[str, Any]:
        """Fetch metadata for a given file_id."""
        service = self._get_drive_service()
        try:
            return service.files().get(
                fileId=file_id,
                fields="id, name, size, md5Checksum, modifiedTime, trashed",
            ).execute()
        except Exception as e:
            sanitized = sanitize_error(str(e))
            raise GoogleDriveError(f"Failed to get metadata for {file_id}: {sanitized}") from e

    def rename_file(self, file_id: str, new_name: str) -> Dict[str, Any]:
        """Rename an existing remote file (used during staging promotion)."""
        service = self._get_drive_service()
        try:
            return service.files().update(
                fileId=file_id,
                body={"name": new_name},
                fields="id, name, size",
            ).execute()
        except Exception as e:
            sanitized = sanitize_error(str(e))
            logger.error(f"Failed to rename Google Drive file {file_id} to {new_name}: {sanitized}")
            raise GoogleDriveError(f"Rename failed for {file_id}: {sanitized}") from e

    def delete_file(self, file_id: str) -> bool:
        """Permanently delete a remote file by ID."""
        service = self._get_drive_service()
        try:
            service.files().delete(fileId=file_id).execute()
            logger.info(f"Deleted old remote backup file {file_id}")
            return True
        except Exception as e:
            sanitized = sanitize_error(str(e))
            logger.warning(f"Failed to delete remote file {file_id}: {sanitized}")
            return False

    def sync_backup(
        self,
        local_encrypted_path: Union[str, Path],
        local_checksum_path: Union[str, Path],
        canonical_backup_name: str = DEFAULT_BACKUP_FILENAME,
        canonical_checksum_name: str = DEFAULT_CHECKSUM_FILENAME,
    ) -> Dict[str, Any]:
        """Execute the atomic Phase 5 remote backup replacement pipeline.
        
        Pipeline:
        1. Identify existing remote canonical backups (to delete only AFTER verification).
        2. Upload new artifact with unique staging name.
        3. Upload checksum with unique staging name.
        4. Verify remote staging file exists and size matches.
        5. Download remote staging artifact to temporary directory.
        6. Verify downloaded SHA-256 matches local checksum.
        7. Only after verification passes:
           a. Promote staging files to canonical names.
           b. Delete previous remote canonical backup and checksum files.
        8. Verify promoted canonical remote files.
        9. Cleanup local temporary download.
        
        If ANY step fails, previous remote backups remain untouched.
        """
        enc_file = Path(local_encrypted_path)
        chk_file = Path(local_checksum_path)

        if not enc_file.is_file():
            return {
                "status": "FAILED",
                "error": f"Local encrypted backup file not found: {local_encrypted_path}",
            }
        if not chk_file.is_file():
            return {
                "status": "FAILED",
                "error": f"Local checksum file not found: {local_checksum_path}",
            }

        staging_suffix = f"staging.{uuid.uuid4().hex[:8]}"
        staging_backup_name = f"{canonical_backup_name}.{staging_suffix}"
        staging_checksum_name = f"{canonical_checksum_name}.{staging_suffix}"

        staging_backup_id = None
        staging_checksum_id = None
        temp_dir = None

        try:
            # 1. Query existing canonical backups before doing anything
            previous_backups = self.find_files(canonical_backup_name)
            previous_checksums = self.find_files(canonical_checksum_name)

            # 2. Upload staging backup artifact
            logger.info(f"Uploading new backup to Google Drive as staging artifact: {staging_backup_name}")
            backup_meta = self.upload_file(enc_file, staging_backup_name)
            staging_backup_id = backup_meta.get("id")

            # 3. Upload staging checksum artifact
            logger.info(f"Uploading checksum to Google Drive as staging artifact: {staging_checksum_name}")
            checksum_meta = self.upload_file(chk_file, staging_checksum_name, mime_type="text/plain")
            staging_checksum_id = checksum_meta.get("id")

            # 4. Verify remote staging file exists and size matches
            remote_meta = self.get_file_metadata(staging_backup_id)
            remote_size = int(remote_meta.get("size", -1))
            local_size = enc_file.stat().st_size
            if remote_size != local_size:
                raise GoogleDriveVerificationError(
                    f"Remote staging size mismatch: local={local_size}, remote={remote_size}"
                )

            # 5. Download remote staging artifact to temp local dir and verify SHA-256
            temp_dir = tempfile.mkdtemp(prefix="pm_gdrive_verify_")
            downloaded_temp_file = Path(temp_dir) / "downloaded.enc"
            self.download_file(staging_backup_id, downloaded_temp_file)

            # 6. Verify SHA-256 of downloaded copy
            local_expected_sha256 = calculate_sha256(enc_file)
            if not verify_sha256(downloaded_temp_file, local_expected_sha256):
                raise GoogleDriveVerificationError(
                    "SHA-256 checksum verification failed on downloaded staging remote artifact."
                )
            logger.info("[PASS] Remote staging backup downloaded and SHA-256 verified successfully.")

            # 7. Promotion: rename staging files to canonical names
            self.rename_file(staging_backup_id, canonical_backup_name)
            self.rename_file(staging_checksum_id, canonical_checksum_name)

            # 8. Delete previous remote backups now that new canonical backup is active and verified
            for prev in previous_backups:
                if prev.get("id") != staging_backup_id:
                    self.delete_file(prev["id"])
            for prev_chk in previous_checksums:
                if prev_chk.get("id") != staging_checksum_id:
                    self.delete_file(prev_chk["id"])

            logger.info(f"[SUCCESS] Promoted new remote backup '{canonical_backup_name}' (ID: {staging_backup_id}).")

            return {
                "status": "SUCCESS",
                "remote_file_id": staging_backup_id,
                "remote_checksum_file_id": staging_checksum_id,
                "backup_filename": canonical_backup_name,
                "checksum_filename": canonical_checksum_name,
                "sha256": local_expected_sha256,
                "encrypted_size": local_size,
                "error": None,
            }

        except Exception as e:
            sanitized = sanitize_error(str(e))
            logger.error(f"Google Drive backup sync failed: {sanitized}")

            # Clean up staging files if any were created to avoid cluttering remote folder
            if staging_backup_id:
                try:
                    self.delete_file(staging_backup_id)
                except Exception:
                    pass
            if staging_checksum_id:
                try:
                    self.delete_file(staging_checksum_id)
                except Exception:
                    pass

            return {
                "status": "FAILED",
                "error": sanitized,
                "remote_file_id": None,
                "remote_checksum_file_id": None,
            }

        finally:
            # Clean up local temporary verification directory
            if temp_dir and os.path.isdir(temp_dir):
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)
