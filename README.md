# LinkedIn Job Search Agent

A daily job-search pipeline that scrapes LinkedIn for the roles *you*
define, filters out the noise, scores every posting against your
preferences, and emails you a ranked shortlist every morning — all running
free on GitHub Actions.

It works for **any field**, not just tech: every keyword, filter, and salary
threshold comes from one YAML config file. It has run in production for a
DevOps engineer and a supply-chain analyst off the same codebase.

```
LinkedIn (guest API, last 24h, your keywords × your regions)
   → deduplicate against everything you've already seen
   → drop non-English postings
   → score 0–100 against your config (role match, salary, remote, seniority)
   → email you the A/B-tier shortlist, full scored data attached
```

No LLM calls, no API costs, no LinkedIn login. The only account you need is
a Gmail address to send the email from.

## What the email looks like

```
Subject: Job Shortlist — 2026-08-25 | 3 A-tier, 7 B-tier

=== A-TIER ROLES (3) ===

[Score: 88] ExampleCorp — Platform Engineer
Location: Dublin, Ireland | Hybrid
Salary: €55,000 – €65,000
Apply: https://www.linkedin.com/jobs/view/…

...
```

## Quickstart

**1. Get your own copy.** Click **Use this template → Create a new
repository** (top-right of this repo's GitHub page) and choose
**Private**. Private matters: your copy will contain your email address,
salary targets, and job-hunt history. (A plain fork works too, but forks
of public repos can't be private and scheduled workflows are disabled in
forks by default.)

**2. Tell it who you are.** Edit `users/me/config.yaml` — your email, the
job titles to search, your locations and salary floors. Every field is
documented in `users/example/config.yaml`. Not sure what to write? Fill in
`onboarding_form.md` instead and paste it into an AI assistant (e.g. Claude)
with the repo open — it generates the config for you.

**3. Set up email sending.** Follow [SETUP_EMAIL.md](SETUP_EMAIL.md) — a
one-time, ~10-minute Gmail API setup on your own machine, then paste the two
resulting files into the repo secrets `GMAIL_CREDENTIALS_JSON` and
`GMAIL_TOKEN_JSON`.

**4. Test it** (optional but recommended), locally:

```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python orchestrator.py --dry-run     # scrape + score, no email — takes a while
python send_morning_email.py --dry-run   # preview the email
```

**5. Turn on the schedule.** Open the **Actions** tab and enable workflows.
That's it — the pipeline now runs every weekday at 07:00 UTC. Run it on
demand any time via **Actions → Daily Job Search → Run workflow**.

## Changing the schedule

Edit the cron line in `.github/workflows/job-search.yml`:

```yaml
schedule:
  - cron: '0 7 * * 1-5'   # minute hour day month weekday — in UTC
```

`'0 7 * * 1-5'` = 07:00 UTC Monday–Friday. Cron times are **UTC**: if you
want 08:00 Dublin time in summer (UTC+1), use `0 7`; for 08:00 in winter,
use `0 8`. [crontab.guru](https://crontab.guru) helps.

Two caveats from GitHub's side: scheduled runs can start a few minutes late
at busy times, and GitHub pauses schedules in repos with no activity for 60
days (the daily seen-jobs commit counts as activity, so an active setup
never hits this — but if you pause for months, re-enable in the Actions tab).

## How scoring works

Each job gets 0–100 points, driven entirely by your config:

| Component | Points | Driven by |
|---|---|---|
| Role / keyword match | 0–40 | `tech_terms`, `role_titles`, `core_tech_terms` |
| Salary vs your targets | 0–25 | per-region `min_salary` / `target_salary` |
| Remote / hybrid | 5–10 | posting's work model |
| Seniority fit | 10–20 | `max_years_exp`, stated experience requirements |
| Company quality | 3 | static |
| Visa sponsorship bonus | 0–10 | `visa_signals` / `visa_large_mncs` |

**Tier A** (80+) and **Tier B** (60–79) are emailed, best first. **Tier C**
(40–59) is kept in the scored file for reference but never emailed.

Jobs are skipped outright (never emailed, never scored) when they hit a
dealbreaker: a company on your avoid list, an excluded title term
(`excluded_role_terms`, seniority filter), a listed salary below your
regional minimum, more required experience than `max_years_exp`, a
description dominated by languages/skills you've excluded, or — for broad
country regions — a location outside your `urban_centers` list.

Every emailed job carries a `match_reason` explaining its score, and the
full scored JSON is attached to each email, so tuning your config is a
feedback loop: see a bad match, add the term that would have filtered it.

## Repo layout

| Path | Purpose |
|---|---|
| `users/me/config.yaml` | **Your** search config — the only file you must edit |
| `users/example/config.yaml` | Fully documented reference config |
| `onboarding_form.md` | Plain-language form an AI can turn into your config |
| `orchestrator.py` | The daily run: scrape → dedupe → score → email |
| `scrapers/linkedin.py` | LinkedIn guest-API scraper (rate-limited, backoff) |
| `scorer.py` | Pure-Python scoring against your profile |
| `deduplicator.py` | 3-layer dedup (URL, ID, fuzzy title+company) |
| `send_morning_email.py` | Gmail API sender |
| `user_profile.py` | Loads and validates `users/<name>/config.yaml` |
| `users/me/seen_jobs.json` | Jobs already seen — committed back by the workflow |
| `companies_to_avoid.md` | One company per line, skipped before scoring |
| `.github/workflows/job-search.yml` | The schedule |

## Running for more than one person

One repo can serve several people (each gets their own email and history):

1. Create `users/<name>/config.yaml` (copy `users/example/config.yaml`)
2. Duplicate `.github/workflows/job-search.yml` as `<name>-job-search.yml`,
   set `JOB_USER: <name>` in its `env:` block, and give it a different hour
   so the two runs never scrape LinkedIn at the same time
3. Emails for every user are sent from the single authorized Gmail account

## Running locally instead of GitHub Actions

The pipeline is a plain Python script — a local cron (or Windows Task
Scheduler) works fine:

```
0 7 * * 1-5  cd /path/to/repo && ./venv/bin/python orchestrator.py >> logs/morning.log 2>&1
```

## Privacy

- Your copy of this repo contains **your** email address and preferences in
  `users/me/config.yaml`, and your job-viewing history in
  `users/me/seen_jobs.json` (the workflow commits it after each run).
  **Keep your copy private.**
- `credentials.json` / `token.json` are Gmail secrets: they live only in
  your repo's Actions secrets and on your own machine, never in git.
  `.gitignore` already covers them.
- CV files: `.gitignore` excludes `*.pdf`/`*.docx` so a résumé dropped into
  the folder isn't committed by accident.

## Troubleshooting

**No email arrived** — check the Actions run log first, then your spam
folder. Most common causes: missing/expired `GMAIL_TOKEN_JSON` secret (see
SETUP_EMAIL.md), or every job was filtered (the log says how many scored).

**0 jobs scraped** — your `role_keywords` may be too narrow for the last-24h
window, or LinkedIn is throttling (the log shows HTTP 429 retries; the
scraper backs off automatically and per-domain rate limits are deliberately
conservative — don't lower them).

**Wrong jobs getting through** — add title terms to `excluded_role_terms`,
description phrases to `excluded_description_terms`, or tighten
`core_tech_terms`.

**Everything filtered out** — loosen `max_years_exp`, lower `min_salary`
(or set it to 0), or check that your `core_tech_terms` aren't stricter than
the postings in your field actually are.

## A note on scraping

This uses LinkedIn's public guest API — the same data you'd see logged out
in a browser — with conservative rate limits, exponential backoff, and a
24-hour window to keep request volume minimal. It may still conflict with
LinkedIn's Terms of Service, scraping etiquette is your responsibility, and
the endpoints can change without notice and break the scraper. Use at your
own risk, keep the shipped rate limits, and don't run it more than daily.
This project is not affiliated with or endorsed by LinkedIn.
