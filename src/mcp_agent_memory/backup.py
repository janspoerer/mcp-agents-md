"""
Backup System for MCP Agent Memory Server

Supports:
- Google Drive backup with automatic rotation
- Amazon S3 backup with automatic rotation
- Email backup with SMTP
"""

import os
import ssl
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from typing import Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from dotenv import load_dotenv

# Optional S3 support
try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
    S3_AVAILABLE = True
except ImportError:
    S3_AVAILABLE = False

# =============================================================================
# Configuration
# =============================================================================

load_dotenv()

# File settings
FILE_TO_BACKUP = os.getenv("MEMORY_FILE_PATH", "AGENTS.md")
BACKUP_RETENTION_DAYS = int(os.getenv("BACKUP_RETENTION_DAYS", "30"))

# Google Drive settings
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
PARENT_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
GDRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# S3 settings
S3_ENABLED = os.getenv("S3_BACKUP_ENABLED", "false").lower() == "true"
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_PREFIX = os.getenv("S3_PREFIX", "mcp-backups/")  # Folder prefix in bucket
S3_REGION = os.getenv("S3_REGION", "us-east-1")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
# Optional: Use AWS_PROFILE for credential profiles instead of keys
AWS_PROFILE = os.getenv("AWS_PROFILE", "")

# Email settings
EMAIL_ENABLED = os.getenv("EMAIL_BACKUP_ENABLED", "false").lower() == "true"
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")  # App password for Gmail
EMAIL_FROM = os.getenv("EMAIL_FROM", "")
EMAIL_TO = os.getenv("EMAIL_TO", "")  # Comma-separated list of recipients
EMAIL_SUBJECT_PREFIX = os.getenv("EMAIL_SUBJECT_PREFIX", "[MCP Backup]")

# =============================================================================
# Logging Setup
# =============================================================================

logging.basicConfig(
    filename="backup.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("backup")

# Also log to console
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(console_handler)


# =============================================================================
# Google Drive Backup
# =============================================================================

def get_drive_service():
    """Create and return authenticated Google Drive service."""
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        raise FileNotFoundError(
            f"Service account file not found: {SERVICE_ACCOUNT_FILE}. "
            f"Please download it from Google Cloud Console."
        )

    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=GDRIVE_SCOPES
    )
    return build("drive", "v3", credentials=creds)


def backup_to_gdrive() -> Optional[str]:
    """
    Upload AGENTS.md to Google Drive with timestamp.

    Returns:
        File ID of the uploaded backup, or None if failed.
    """
    if not PARENT_FOLDER_ID:
        logger.error("GOOGLE_DRIVE_FOLDER_ID not set in .env")
        return None

    if not os.path.exists(FILE_TO_BACKUP):
        logger.error(f"File to backup not found: {FILE_TO_BACKUP}")
        return None

    try:
        service = get_drive_service()

        # Create timestamped filename
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_filename = f"AGENTS_backup_{timestamp}.md"

        file_metadata = {
            "name": backup_filename,
            "parents": [PARENT_FOLDER_ID],
            "description": f"Backup of AGENTS.md from {timestamp}"
        }

        # Get file size for logging
        file_size = os.path.getsize(FILE_TO_BACKUP)

        media = MediaFileUpload(
            FILE_TO_BACKUP,
            mimetype="text/markdown",
            resumable=True if file_size > 5 * 1024 * 1024 else False
        )

        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, name, size, createdTime"
        ).execute()

        logger.info(
            f"Google Drive backup successful: {file.get('name')} "
            f"(ID: {file.get('id')}, Size: {file.get('size')} bytes)"
        )
        return file.get("id")

    except HttpError as e:
        logger.error(f"Google Drive API error: {e}")
        return None
    except Exception as e:
        logger.error(f"Google Drive backup failed: {e}")
        return None


def cleanup_old_backups(days_to_keep: int = BACKUP_RETENTION_DAYS) -> int:
    """
    Delete backups older than specified days from Google Drive.

    Args:
        days_to_keep: Number of days to retain backups.

    Returns:
        Number of files deleted.
    """
    if not PARENT_FOLDER_ID:
        logger.error("GOOGLE_DRIVE_FOLDER_ID not set, cannot cleanup")
        return 0

    try:
        service = get_drive_service()

        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        cutoff_str = cutoff_date.strftime("%Y-%m-%dT%H:%M:%S")

        query = (
            f"'{PARENT_FOLDER_ID}' in parents and "
            f"name contains 'AGENTS_backup_' and "
            f"createdTime < '{cutoff_str}' and "
            f"trashed = false"
        )

        results = service.files().list(
            q=query,
            fields="files(id, name, createdTime)",
            orderBy="createdTime asc"
        ).execute()

        files_to_delete = results.get("files", [])

        if not files_to_delete:
            logger.info(f"No backups older than {days_to_keep} days found")
            return 0

        deleted_count = 0
        for file in files_to_delete:
            try:
                service.files().delete(fileId=file["id"]).execute()
                logger.info(
                    f"Deleted old backup: {file['name']} "
                    f"(created: {file['createdTime']})"
                )
                deleted_count += 1
            except HttpError as e:
                logger.error(f"Failed to delete {file['name']}: {e}")

        logger.info(f"Cleanup complete: deleted {deleted_count} old backups")
        return deleted_count

    except HttpError as e:
        logger.error(f"Google Drive API error during cleanup: {e}")
        return 0
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        return 0


def list_backups() -> list:
    """List all existing backups in Google Drive."""
    if not PARENT_FOLDER_ID:
        logger.error("GOOGLE_DRIVE_FOLDER_ID not set")
        return []

    try:
        service = get_drive_service()

        query = (
            f"'{PARENT_FOLDER_ID}' in parents and "
            f"name contains 'AGENTS_backup_' and "
            f"trashed = false"
        )

        results = service.files().list(
            q=query,
            fields="files(id, name, size, createdTime)",
            orderBy="createdTime desc"
        ).execute()

        files = results.get("files", [])
        logger.info(f"Found {len(files)} backups in Google Drive")
        return files

    except Exception as e:
        logger.error(f"Failed to list backups: {e}")
        return []


def get_backup_stats() -> dict:
    """Get statistics about current Google Drive backups."""
    backups = list_backups()

    if not backups:
        return {
            "count": 0,
            "total_size_bytes": 0,
            "oldest": None,
            "newest": None
        }

    total_size = sum(int(b.get("size", 0)) for b in backups)

    return {
        "count": len(backups),
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "oldest": backups[-1]["createdTime"] if backups else None,
        "newest": backups[0]["createdTime"] if backups else None,
        "retention_days": BACKUP_RETENTION_DAYS
    }


# =============================================================================
# Amazon S3 Backup
# =============================================================================

def get_s3_client():
    """Create and return an S3 client."""
    if not S3_AVAILABLE:
        raise ImportError("boto3 is not installed. Run: pip install boto3")

    if AWS_PROFILE:
        # Use named profile from ~/.aws/credentials
        session = boto3.Session(profile_name=AWS_PROFILE)
        return session.client("s3", region_name=S3_REGION)
    elif AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
        # Use explicit credentials
        return boto3.client(
            "s3",
            region_name=S3_REGION,
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY
        )
    else:
        # Use default credential chain (IAM role, env vars, etc.)
        return boto3.client("s3", region_name=S3_REGION)


def backup_to_s3() -> Optional[str]:
    """
    Upload AGENTS.md to Amazon S3 with timestamp.

    Returns:
        S3 object key of the uploaded backup, or None if failed.
    """
    if not S3_ENABLED:
        logger.info("S3 backup is disabled (S3_BACKUP_ENABLED=false)")
        return None

    if not S3_AVAILABLE:
        logger.error("S3 backup requested but boto3 is not installed")
        return None

    if not S3_BUCKET:
        logger.error("S3_BUCKET not set in .env")
        return None

    if not os.path.exists(FILE_TO_BACKUP):
        logger.error(f"File to backup not found: {FILE_TO_BACKUP}")
        return None

    try:
        s3 = get_s3_client()

        # Create timestamped filename
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_key = f"{S3_PREFIX}AGENTS_backup_{timestamp}.md"

        # Upload file
        s3.upload_file(
            FILE_TO_BACKUP,
            S3_BUCKET,
            backup_key,
            ExtraArgs={
                "ContentType": "text/markdown",
                "Metadata": {
                    "backup-timestamp": timestamp,
                    "source": "mcp-agent-memory"
                }
            }
        )

        logger.info(f"S3 backup successful: s3://{S3_BUCKET}/{backup_key}")
        return backup_key

    except NoCredentialsError:
        logger.error("S3 backup failed: AWS credentials not found")
        logger.error("Set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY or AWS_PROFILE")
        return None
    except ClientError as e:
        logger.error(f"S3 API error: {e}")
        return None
    except Exception as e:
        logger.error(f"S3 backup failed: {e}")
        return None


def cleanup_old_s3_backups(days_to_keep: int = BACKUP_RETENTION_DAYS) -> int:
    """
    Delete S3 backups older than specified days.

    Args:
        days_to_keep: Number of days to retain backups.

    Returns:
        Number of objects deleted.
    """
    if not S3_ENABLED or not S3_AVAILABLE or not S3_BUCKET:
        return 0

    try:
        s3 = get_s3_client()
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_to_keep)

        # List objects with our prefix
        paginator = s3.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=S3_BUCKET, Prefix=S3_PREFIX)

        objects_to_delete = []
        for page in pages:
            for obj in page.get("Contents", []):
                if "AGENTS_backup_" in obj["Key"]:
                    if obj["LastModified"] < cutoff_date:
                        objects_to_delete.append({"Key": obj["Key"]})

        if not objects_to_delete:
            logger.info(f"No S3 backups older than {days_to_keep} days found")
            return 0

        # Delete in batches (S3 allows up to 1000 per request)
        deleted_count = 0
        for i in range(0, len(objects_to_delete), 1000):
            batch = objects_to_delete[i:i+1000]
            response = s3.delete_objects(
                Bucket=S3_BUCKET,
                Delete={"Objects": batch}
            )
            deleted_count += len(response.get("Deleted", []))

        logger.info(f"S3 cleanup complete: deleted {deleted_count} old backups")
        return deleted_count

    except Exception as e:
        logger.error(f"S3 cleanup failed: {e}")
        return 0


def list_s3_backups() -> list:
    """List all existing backups in S3."""
    if not S3_ENABLED or not S3_AVAILABLE or not S3_BUCKET:
        return []

    try:
        s3 = get_s3_client()
        paginator = s3.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=S3_BUCKET, Prefix=S3_PREFIX)

        backups = []
        for page in pages:
            for obj in page.get("Contents", []):
                if "AGENTS_backup_" in obj["Key"]:
                    backups.append({
                        "key": obj["Key"],
                        "size": obj["Size"],
                        "last_modified": obj["LastModified"].isoformat()
                    })

        # Sort by date, newest first
        backups.sort(key=lambda x: x["last_modified"], reverse=True)
        logger.info(f"Found {len(backups)} backups in S3")
        return backups

    except Exception as e:
        logger.error(f"Failed to list S3 backups: {e}")
        return []


def get_s3_backup_stats() -> dict:
    """Get statistics about current S3 backups."""
    backups = list_s3_backups()

    if not backups:
        return {
            "count": 0,
            "total_size_bytes": 0,
            "oldest": None,
            "newest": None
        }

    total_size = sum(b.get("size", 0) for b in backups)

    return {
        "count": len(backups),
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "oldest": backups[-1]["last_modified"] if backups else None,
        "newest": backups[0]["last_modified"] if backups else None,
        "bucket": S3_BUCKET,
        "prefix": S3_PREFIX
    }


# =============================================================================
# Email Backup
# =============================================================================

def backup_to_email() -> bool:
    """
    Send the AGENTS.md content via email.

    The memory file content is sent as the email body in plain text.
    This provides a simple backup mechanism that works with any email provider.

    Returns:
        True if email sent successfully, False otherwise.
    """
    if not EMAIL_ENABLED:
        logger.info("Email backup is disabled (EMAIL_BACKUP_ENABLED=false)")
        return False

    # Validate configuration
    missing_config = []
    if not SMTP_USERNAME:
        missing_config.append("SMTP_USERNAME")
    if not SMTP_PASSWORD:
        missing_config.append("SMTP_PASSWORD")
    if not EMAIL_FROM:
        missing_config.append("EMAIL_FROM")
    if not EMAIL_TO:
        missing_config.append("EMAIL_TO")

    if missing_config:
        logger.error(f"Email backup missing configuration: {', '.join(missing_config)}")
        return False

    if not os.path.exists(FILE_TO_BACKUP):
        logger.error(f"File to backup not found: {FILE_TO_BACKUP}")
        return False

    try:
        # Read the memory file
        with open(FILE_TO_BACKUP, "r", encoding="utf-8") as f:
            content = f.read()

        # Create email
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        subject = f"{EMAIL_SUBJECT_PREFIX} Agent Memory Backup - {timestamp}"

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO

        # Plain text version
        text_body = f"""MCP Agent Memory Backup
========================
Date: {timestamp}
File: {FILE_TO_BACKUP}
Size: {len(content)} characters

{'=' * 60}

{content}

{'=' * 60}
End of backup

This is an automated backup from MCP Agent Memory Server.
"""

        # HTML version (for better formatting in email clients)
        html_body = f"""<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
        .header {{ background: #f5f5f5; padding: 20px; border-radius: 8px; margin-bottom: 20px; }}
        .content {{ background: #fafafa; padding: 20px; border-radius: 8px; white-space: pre-wrap; font-family: monospace; font-size: 14px; }}
        .footer {{ color: #666; font-size: 12px; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="header">
        <h2>MCP Agent Memory Backup</h2>
        <p><strong>Date:</strong> {timestamp}</p>
        <p><strong>File:</strong> {FILE_TO_BACKUP}</p>
        <p><strong>Size:</strong> {len(content)} characters</p>
    </div>
    <div class="content">{content}</div>
    <div class="footer">
        <p>This is an automated backup from MCP Agent Memory Server.</p>
    </div>
</body>
</html>
"""

        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        # Parse recipients
        recipients = [r.strip() for r in EMAIL_TO.split(",") if r.strip()]

        # Send email
        logger.info(f"Connecting to SMTP server {SMTP_HOST}:{SMTP_PORT}...")

        if SMTP_USE_TLS:
            # Use STARTTLS (port 587)
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.ehlo()
                context = ssl.create_default_context()
                server.starttls(context=context)
                server.ehlo()
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.sendmail(EMAIL_FROM, recipients, msg.as_string())
        else:
            # Use SSL directly (port 465)
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=30) as server:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.sendmail(EMAIL_FROM, recipients, msg.as_string())

        logger.info(f"Email backup sent successfully to {len(recipients)} recipient(s)")
        return True

    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"SMTP authentication failed: {e}")
        logger.error("For Gmail, use an App Password: https://myaccount.google.com/apppasswords")
        return False
    except smtplib.SMTPException as e:
        logger.error(f"SMTP error: {e}")
        return False
    except Exception as e:
        logger.error(f"Email backup failed: {e}")
        return False


def test_email_config() -> bool:
    """
    Test email configuration by sending a test email.

    Returns:
        True if test email sent successfully.
    """
    if not EMAIL_ENABLED:
        print("Email backup is disabled. Set EMAIL_BACKUP_ENABLED=true in .env")
        return False

    # Validate configuration
    print("Checking email configuration...")
    print(f"  SMTP_HOST: {SMTP_HOST}")
    print(f"  SMTP_PORT: {SMTP_PORT}")
    print(f"  SMTP_USE_TLS: {SMTP_USE_TLS}")
    print(f"  SMTP_USERNAME: {'*' * len(SMTP_USERNAME) if SMTP_USERNAME else '(not set)'}")
    print(f"  SMTP_PASSWORD: {'*' * 8 if SMTP_PASSWORD else '(not set)'}")
    print(f"  EMAIL_FROM: {EMAIL_FROM or '(not set)'}")
    print(f"  EMAIL_TO: {EMAIL_TO or '(not set)'}")

    if not all([SMTP_USERNAME, SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO]):
        print("\nError: Missing required email configuration")
        return False

    print("\nSending test email...")

    try:
        msg = MIMEText("This is a test email from MCP Agent Memory Server.\n\nYour email backup is configured correctly!")
        msg["Subject"] = f"{EMAIL_SUBJECT_PREFIX} Test Email"
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO

        recipients = [r.strip() for r in EMAIL_TO.split(",") if r.strip()]

        if SMTP_USE_TLS:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.sendmail(EMAIL_FROM, recipients, msg.as_string())
        else:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=30) as server:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.sendmail(EMAIL_FROM, recipients, msg.as_string())

        print(f"Test email sent successfully to: {EMAIL_TO}")
        return True

    except Exception as e:
        print(f"Test email failed: {e}")
        return False


# =============================================================================
# Main Entry Point
# =============================================================================

def run_backup_job(
    include_gdrive: bool = True,
    include_s3: bool = True,
    include_email: bool = True
):
    """
    Run the complete backup job.

    Args:
        include_gdrive: Run Google Drive backup
        include_s3: Run S3 backup
        include_email: Run email backup
    """
    logger.info("=" * 60)
    logger.info("Starting backup job...")

    gdrive_success = False
    s3_success = False
    email_success = False

    # Google Drive backup
    if include_gdrive and PARENT_FOLDER_ID:
        logger.info("Running Google Drive backup...")
        file_id = backup_to_gdrive()
        gdrive_success = file_id is not None

        if gdrive_success:
            cleanup_old_backups()
            stats = get_backup_stats()
            logger.info(
                f"Google Drive stats: {stats['count']} backups, "
                f"{stats['total_size_mb']} MB total"
            )
    elif include_gdrive:
        logger.info("Google Drive backup skipped (GOOGLE_DRIVE_FOLDER_ID not set)")

    # S3 backup
    if include_s3 and S3_ENABLED:
        logger.info("Running S3 backup...")
        s3_key = backup_to_s3()
        s3_success = s3_key is not None

        if s3_success:
            cleanup_old_s3_backups()
            stats = get_s3_backup_stats()
            logger.info(
                f"S3 stats: {stats['count']} backups, "
                f"{stats['total_size_mb']} MB total"
            )
    elif include_s3:
        logger.info("S3 backup skipped (S3_BACKUP_ENABLED=false)")

    # Email backup
    if include_email and EMAIL_ENABLED:
        logger.info("Running email backup...")
        email_success = backup_to_email()
    elif include_email:
        logger.info("Email backup skipped (EMAIL_BACKUP_ENABLED=false)")

    # Summary
    logger.info("-" * 40)
    logger.info("Backup job complete:")
    logger.info(f"  Google Drive: {'SUCCESS' if gdrive_success else 'SKIPPED/FAILED'}")
    logger.info(f"  S3:           {'SUCCESS' if s3_success else 'SKIPPED/FAILED'}")
    logger.info(f"  Email:        {'SUCCESS' if email_success else 'SKIPPED/FAILED'}")
    logger.info("=" * 60)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "backup":
            run_backup_job()
        elif command == "gdrive":
            backup_to_gdrive()
        elif command == "s3":
            backup_to_s3()
        elif command == "email":
            backup_to_email()
        elif command == "test-email":
            test_email_config()
        elif command == "cleanup":
            cleanup_old_backups()
            cleanup_old_s3_backups()
        elif command == "cleanup-gdrive":
            cleanup_old_backups()
        elif command == "cleanup-s3":
            cleanup_old_s3_backups()
        elif command == "list":
            print("=== Google Drive Backups ===")
            backups = list_backups()
            for b in backups:
                print(f"  {b['createdTime']} - {b['name']} ({b.get('size', '?')} bytes)")
            print("")
            print("=== S3 Backups ===")
            s3_backups = list_s3_backups()
            for b in s3_backups:
                print(f"  {b['last_modified']} - {b['key']} ({b.get('size', '?')} bytes)")
        elif command == "list-gdrive":
            backups = list_backups()
            for b in backups:
                print(f"{b['createdTime']} - {b['name']} ({b.get('size', '?')} bytes)")
        elif command == "list-s3":
            backups = list_s3_backups()
            for b in backups:
                print(f"{b['last_modified']} - {b['key']} ({b.get('size', '?')} bytes)")
        elif command == "stats":
            print("=== Google Drive ===")
            stats = get_backup_stats()
            print(f"  Backups: {stats['count']}")
            print(f"  Total size: {stats.get('total_size_mb', 0)} MB")
            print(f"  Oldest: {stats['oldest']}")
            print(f"  Newest: {stats['newest']}")
            print("")
            print("=== S3 ===")
            s3_stats = get_s3_backup_stats()
            print(f"  Bucket: {s3_stats.get('bucket', 'N/A')}")
            print(f"  Backups: {s3_stats['count']}")
            print(f"  Total size: {s3_stats.get('total_size_mb', 0)} MB")
            print(f"  Oldest: {s3_stats['oldest']}")
            print(f"  Newest: {s3_stats['newest']}")
            print("")
            print(f"Retention: {BACKUP_RETENTION_DAYS} days")
        else:
            print(f"Unknown command: {command}")
            print("Usage: python -m mcp_agent_memory.backup [command]")
            print("")
            print("Commands:")
            print("  backup        Run full backup (Google Drive + S3 + Email)")
            print("  gdrive        Run Google Drive backup only")
            print("  s3            Run S3 backup only")
            print("  email         Run email backup only")
            print("  test-email    Test email configuration")
            print("  cleanup       Delete old backups (all services)")
            print("  cleanup-gdrive  Delete old Google Drive backups only")
            print("  cleanup-s3    Delete old S3 backups only")
            print("  list          List all backups")
            print("  list-gdrive   List Google Drive backups only")
            print("  list-s3       List S3 backups only")
            print("  stats         Show backup statistics")
            sys.exit(1)
    else:
        run_backup_job()
