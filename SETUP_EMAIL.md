# Email Setup — Gmail API Configuration

The daily shortlist is sent through the Gmail API from **your own Gmail
account**. This is a one-time setup, done on your own computer, and takes
about 10 minutes. Nothing here costs money.

The email is sent FROM the Gmail account you authorize below, TO the address
in your `users/me/config.yaml` (they can be the same account — for a personal
setup they usually are).

## One-Time Setup (OAuth — recommended)

### 1. Create a Google Cloud project

- Go to [Google Cloud Console](https://console.cloud.google.com) (free)
- Create a new project (any name, e.g. "job-search")
- In **APIs & Services → Library**, search for "Gmail API" and click **Enable**

### 2. Create OAuth2 credentials

- **APIs & Services → Credentials** → **Create Credentials** → **OAuth client ID**
- If prompted, configure the consent screen first: choose **External**, fill
  in only the required fields, and add your own Gmail address as a test user
- Application type: **Desktop app**
- Download the JSON file and save it as `credentials.json` in the project root

### 3. Authorize

```bash
python send_morning_email.py --auth-setup
```

A browser window opens — sign in with the Gmail account that should SEND the
emails and grant permission. A `token.json` is saved next to `credentials.json`.

> `credentials.json` and `token.json` are both in `.gitignore`. **Never commit
> them** — anyone with those files can send email as you.

### 4. Test

```bash
python send_morning_email.py --user me --dry-run   # preview, no send
python send_morning_email.py --user me             # actually sends
```

## Wiring it into GitHub Actions

The scheduled workflow can't open a browser, so it reuses the two files you
just created, stored as repository **secrets**:

1. In your repo: **Settings → Secrets and variables → Actions → New repository secret**
2. Create `GMAIL_CREDENTIALS_JSON` — paste the entire contents of `credentials.json`
3. Create `GMAIL_TOKEN_JSON` — paste the entire contents of `token.json`

The workflow writes these back to files at the start of every run.

## Alternative: Service Account (advanced)

For Google Workspace accounts with domain-wide delegation, a service account
key saved as `service-account.json` also works (it's tried first). Personal
Gmail accounts can't use this — stick with OAuth above.

## Troubleshooting

**"No Gmail credentials found"** — `credentials.json` is missing from the
project root (or, in CI, the `GMAIL_CREDENTIALS_JSON` secret is missing).

**"OAuth token expired" / token errors in CI** — re-run
`python send_morning_email.py --auth-setup` locally, then update the
`GMAIL_TOKEN_JSON` secret with the new `token.json` contents. Tokens for
OAuth apps left in "Testing" mode expire after 7 days — publishing the app
(OAuth consent screen → **Publish app**) gives you a long-lived token.

**"Email sent but not received"** — check the spam folder, and verify the
`email:` field in `users/me/config.yaml`.

**"Access blocked" during --auth-setup** — add your Gmail address as a test
user on the OAuth consent screen, or publish the app.
