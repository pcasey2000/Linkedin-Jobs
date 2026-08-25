#!/usr/bin/env python3
"""
send_morning_email.py — Standalone email sender for the daily job shortlist.

Reads users/<name>/data/candidates_scored.json, formats the top jobs by score,
and sends them via the Gmail API to the email address in the user's config.
The email is sent FROM whichever Gmail account authorized the OAuth token.

Usage:
  python send_morning_email.py                    # send for users/me
  python send_morning_email.py --user alex        # send for users/alex
  python send_morning_email.py --dry-run          # preview only, don't send
  python send_morning_email.py --auth-setup       # first-time OAuth setup

Setup (see SETUP_EMAIL.md for the full guide):
  1. Create Gmail API credentials at https://console.cloud.google.com
  2. Download credentials.json and save to the project root
  3. Run: python send_morning_email.py --auth-setup
  4. Grant permission when prompted (opens browser)
  5. token.json is auto-saved for future runs
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Force UTF-8 on stdout so company names containing emojis or non-Latin
# characters don't crash the preview/log on Windows consoles (cp1252).
# CI on Linux already uses UTF-8 — this is a no-op there.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

try:
    from google.auth.transport.requests import Request
    from google.oauth2.service_account import Credentials
    from google.oauth2.credentials import Credentials as UserCredentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
except ImportError:
    print("ERROR: Google API libraries not installed. Run:")
    print("  pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client")
    sys.exit(1)

from config import MAX_EMAIL_JOBS

BASE_DIR = Path(__file__).parent
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"
SERVICE_ACCOUNT_FILE = BASE_DIR / "service-account.json"

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def load_credentials():
    """Load Gmail API credentials (OAuth or service account)."""
    creds = None

    # Try service account first (no user approval needed)
    if SERVICE_ACCOUNT_FILE.exists():
        try:
            creds = Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE, scopes=GMAIL_SCOPES
            )
            print(f"[Email] Using service account credentials from {SERVICE_ACCOUNT_FILE}")
            return creds
        except Exception as e:
            print(f"[Email] Service account failed: {e}", file=sys.stderr)

    # Fall back to user OAuth
    if TOKEN_FILE.exists():
        try:
            creds = UserCredentials.from_authorized_user_file(TOKEN_FILE, GMAIL_SCOPES)
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
            print(f"[Email] Using OAuth token from {TOKEN_FILE}")
            return creds
        except Exception as e:
            print(f"[Email] Token failed: {e}", file=sys.stderr)
            TOKEN_FILE.unlink(missing_ok=True)

    # No credentials found
    if not CREDENTIALS_FILE.exists():
        print("ERROR: No Gmail credentials found.", file=sys.stderr)
        print("Create OAuth credentials at https://console.cloud.google.com:", file=sys.stderr)
        print("  1. Create a Desktop app (OAuth2)", file=sys.stderr)
        print(f"  2. Download and save as {CREDENTIALS_FILE}", file=sys.stderr)
        print("  3. Run: python send_morning_email.py --auth-setup", file=sys.stderr)
        sys.exit(1)

    return None


def setup_oauth():
    """Interactive OAuth setup — opens browser for user approval."""
    if not CREDENTIALS_FILE.exists():
        print(f"ERROR: {CREDENTIALS_FILE} not found.", file=sys.stderr)
        sys.exit(1)

    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            CREDENTIALS_FILE, GMAIL_SCOPES
        )
        creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())
        print(f"[Email] OAuth setup complete. Token saved to {TOKEN_FILE}")
        return creds
    except Exception as e:
        print(f"ERROR: OAuth setup failed: {e}", file=sys.stderr)
        sys.exit(1)


def load_scored_candidates(scored_file: Path):
    """Load and parse candidates_scored.json from the given path."""
    if not scored_file.exists():
        print(f"ERROR: {scored_file} not found.", file=sys.stderr)
        sys.exit(1)

    try:
        with open(scored_file, encoding="utf-8") as f:
            candidates = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in {scored_file}: {e}", file=sys.stderr)
        sys.exit(1)

    if not candidates:
        print("[Email] candidates_scored.json is empty. No email will be sent.")
        return None

    return candidates


def _format_job_block(job: dict) -> str:
    """One job rendered for the email body. CV line is omitted when not set."""
    block = f"[Score: {job['score']}] {job['company']} — {job['title']}\n"
    block += f"Location: {job['location']} | {job['remote_type'].replace('_', ' ').title()}\n"
    if job.get("salary_raw"):
        block += f"Salary: {job['salary_raw']}\n"
    else:
        block += "Salary: Not listed\n"
    block += f"Apply: {job['url']}\n"
    cv = job.get("cv_to_use")
    if cv:
        block += f"CV: {cv}\n"
    block += "\n"
    return block


def format_email(candidates):
    """Format candidates into email body and subject."""
    if not candidates:
        return None, None

    # Sort by score descending, take the top N
    top = sorted(candidates, key=lambda x: x["score"], reverse=True)[:MAX_EMAIL_JOBS]
    # C-tier jobs are logged in the scored file but never emailed
    top = [j for j in top if j["tier"] in ("A", "B")]

    if not top:
        return None, None

    a_tier = [j for j in top if j["tier"] == "A"]
    b_tier = [j for j in top if j["tier"] == "B"]

    today = datetime.now().strftime("%Y-%m-%d")
    subject = f"Job Shortlist — {today} | {len(a_tier)} A-tier, {len(b_tier)} B-tier"

    body = f"Daily job shortlist for {today}.\n\n"
    body += f"=== A-TIER ROLES ({len(a_tier)}) ===\n\n"
    for job in a_tier:
        body += _format_job_block(job)

    body += f"=== B-TIER ROLES ({len(b_tier)}) ===\n\n"
    for job in b_tier:
        body += _format_job_block(job)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body += f"— Job Bot | {timestamp}\n"

    return subject, body


def send_email(service, subject, body, recipient: str, scored_file: Path):
    """Send email via Gmail API with candidates_scored.json as attachment."""
    try:
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from email.mime.base import MIMEBase
        from email import encoders
        import base64

        # The From header is omitted: Gmail fills it with the account that
        # authorized the OAuth token, which is the only address it can send as.
        message = MIMEMultipart()
        message["to"] = recipient
        message["subject"] = subject

        # Attach text body
        message.attach(MIMEText(body, "plain"))

        # Attach candidates_scored.json (full data, not just the emailed top N)
        if scored_file.exists():
            try:
                attachment = MIMEBase("application", "octet-stream")
                with open(scored_file, "rb") as f:
                    attachment.set_payload(f.read())
                encoders.encode_base64(attachment)
                attachment.add_header(
                    "Content-Disposition",
                    f"attachment; filename={scored_file.name}",
                )
                message.attach(attachment)
                print(f"[Email] Attached {scored_file.name}")
            except Exception as e:
                print(f"[Email] Warning: Failed to attach file: {e}", file=sys.stderr)

        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        send_message = {"raw": raw_message}

        result = service.users().messages().send(
            userId="me", body=send_message
        ).execute()

        print(f"[Email] Email sent successfully. Message ID: {result.get('id')}")
        return True
    except Exception as e:
        print(f"[Email] Failed to send email: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Send daily job shortlist email from candidates_scored.json"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview email without sending",
    )
    parser.add_argument(
        "--auth-setup",
        action="store_true",
        help="Interactive OAuth setup (first-time use)",
    )
    parser.add_argument(
        "--user",
        default="me",
        help="Send email for a named user (reads config from users/<name>/config.yaml)",
    )
    args = parser.parse_args()

    # OAuth setup
    if args.auth_setup:
        setup_oauth()
        return

    # Resolve per-user paths and recipient from the profile
    from user_profile import load_profile
    profile = load_profile(args.user)
    scored_file = Path(profile.data_dir) / "candidates_scored.json"
    recipient = profile.email
    regions_footer = ", ".join(r.id for r in profile.regions)

    # Load credentials (not needed for a dry-run preview)
    creds = None
    if not args.dry_run:
        creds = load_credentials()
        if creds is None:
            print("[Email] No credentials available. Run: python send_morning_email.py --auth-setup")
            sys.exit(1)

    # Load candidates
    candidates = load_scored_candidates(scored_file)
    if candidates is None:
        print("[Email] No candidates to email.")
        return

    # Format email
    subject, body = format_email(candidates)
    if subject is None:
        print("[Email] No A/B-tier jobs to email today.")
        return

    # Append regions footer
    body = body.rstrip() + f"\nRegions: {regions_footer}\n"

    print(f"\n{'='*60}")
    print(f"Email Preview — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")
    print(f"To: {recipient}")
    print(f"Subject: {subject}\n")
    print(body)
    print(f"\n{'='*60}\n")

    if args.dry_run:
        print("[Email] --dry-run: email not sent. Review above.")
        return

    # Send email
    service = build("gmail", "v1", credentials=creds)
    success = send_email(service, subject, body, recipient, scored_file)

    if success:
        shown = body.count("Apply: ")
        total = len(candidates)
        print(f"\nEmail sent: {shown} jobs in shortlist (of {total} scored).")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
