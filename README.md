# Reacher

**Your autonomous job application agent.** Reacher hunts for open roles across LinkedIn and X, finds hiring manager emails, writes personalized applications using AI, and sends them with your resume attached — so you can focus on interview prep instead of cold outreach.

## What Reacher Does

You tell Reacher what roles you're looking for. It takes care of the rest:

1. **Scouts** LinkedIn posts, LinkedIn job listings, and X/Twitter for matching openings
2. **Hunts emails** using multiple strategies — job descriptions, company websites, careers pages, common HR patterns
3. **Picks the best fit** per company (one outreach per company, prioritizing your preferred stack)
4. **Writes tailored emails** powered by Google Gemini, referencing the actual job description and your resume
5. **Queues drafts** for your review — approve, edit, or discard before anything is sent
6. **Sends applications** from your Gmail with your resume attached, then logs everything

Reacher can run on demand or on a schedule. You stay in control — nothing goes out without your say.

## Features

- **Multi-source intelligence** — Scrapes LinkedIn posts (prioritized), LinkedIn jobs, and X/Twitter simultaneously
- **Autonomous email discovery** — Doesn't just check the job description; crawls company websites, careers pages, and generates common HR patterns
- **AI-personalized outreach** — Google Gemini reads the job posting + your resume and writes a unique email for each application
- **Human-in-the-loop** — Full drafts workflow: review, edit, approve, or discard before sending
- **Smart deduplication** — One email per company, with role priority (JS/TS > full stack > frontend > backend)
- **Remote-first** — Filters for remote roles matching your location
- **Scheduled or manual** — Run once or let it work on autopilot every N hours
- **Resume attached** — Your PDF resume goes out with every application automatically
- **Full audit trail** — Every job seen and every email sent is tracked in a local SQLite database

## Quick Start

### 1. Install

```bash
cd reacher
python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp config.yaml.example config.yaml
```

Edit `config.yaml` with your credentials:

| Field | Where to get it |
|---|---|
| `email.app_password` | [Google Account > App Passwords](https://myaccount.google.com/apppasswords) (2FA required) |
| `gemini.api_key` | [Google AI Studio](https://aistudio.google.com/apikey) |
| `twitter.bearer_token` | [X Developer Portal](https://developer.twitter.com/) (free tier) |

### 3. Add your resume

Drop your resume PDF in the project root (filename should match `profile.resume_pdf` in `config.yaml`):

```bash
cp ~/path/to/your/resume.pdf "./Your Resume.pdf"
```

### 4. Let Reacher work

```bash
# Scout for jobs
python -m src run --dry-run

# Generate draft emails
python -m src draft

# Review what Reacher wrote
python -m src drafts
python -m src show-draft 1

# Approve the ones you like
python -m src approve 1 2 3

# Send them out
python -m src send-drafts
```

Or go fully autonomous:

```bash
# Let Reacher find and apply on a schedule
python -m src schedule
```

## Commands

### Scouting

| Command | What it does |
|---|---|
| `run` | Scrape all sources, find new jobs, generate emails, and send |
| `run --dry-run` | Scout for jobs only — nothing is sent |
| `schedule` | Run Reacher on autopilot every N hours (configurable) |
| `schedule --dry-run` | Scheduled scouting without sending |

### Drafts

| Command | What it does |
|---|---|
| `draft` | Generate email drafts for all pending jobs |
| `drafts` | List all drafts with status |
| `drafts --status pending` | Filter by status (`pending`, `approved`, `sent`, `discarded`) |
| `show-draft <id>` | View full email content for a draft |
| `approve <id> [id...]` | Approve specific drafts for sending |
| `approve-all` | Approve all pending drafts |
| `discard <id> [id...]` | Discard drafts you don't want sent |
| `edit-draft <id> --subject "..."` | Edit a draft's subject line |
| `edit-draft <id> --body-file file.txt` | Replace a draft's body from a file |

### Sending

| Command | What it does |
|---|---|
| `send-drafts` | Send all approved drafts |
| `send-drafts --all` | Send all pending + approved drafts |
| `send-drafts --dry-run` | Preview without sending |
| `send-pending` | Send directly (bypasses drafts workflow) |

### Monitoring

| Command | What it does |
|---|---|
| `status` | Jobs discovered, applications sent, draft counts, recent activity |

## How Reacher Thinks

```
  LinkedIn Posts ──┐
  LinkedIn Jobs  ──┼──▶ Filter & Dedup ──▶ Find Emails ──▶ Pick Best per Company
  X / Twitter    ──┘
                                                                   │
                                              ┌────────────────────┘
                                              ▼
                                    Gemini writes email
                                              │
                                              ▼
                                      Save as Draft
                                              │
                                   ┌──────────┼──────────┐
                                   ▼          ▼          ▼
                                Approve     Edit      Discard
                                   │          │
                                   ▼          ▼
                              Send via Gmail + Resume
                                        │
                                        ▼
                                  Log in Database
```

## Configuration

All settings live in `config.yaml`. See `config.yaml.example` for the full template.

```yaml
profile:
  name: "Your Name"
  email: "your.email@gmail.com"
  resume_pdf: "Your Resume.pdf"

search:
  keywords:
    - "software engineer"
    - "full stack developer"
  locations:
    - "remote"
  experience_level: "mid"   # junior | mid | senior

email:
  address: "your.email@gmail.com"
  app_password: "xxxx xxxx xxxx xxxx"
  sender_name: "Your Name"

gemini:
  api_key: "your-gemini-api-key"
  model: "gemini-2.0-flash"

twitter:
  bearer_token: "your-twitter-bearer-token"

schedule:
  interval_hours: 6

limits:
  max_applications_per_run: 10
  max_applications_per_day: 30
```

## Project Structure

```
reacher/
  config.yaml.example      # Example config
  requirements.txt          # Python dependencies
  README.md
  src/
    __init__.py
    __main__.py             # Entry point (python -m src)
    cli.py                  # CLI commands
    agent.py                # Core orchestrator
    config.py               # Config loader + resume parser
    db.py                   # SQLite database layer
    email_finder.py         # Multi-strategy email discovery
    emailer.py              # Gmail SMTP sender
    llm.py                  # Gemini email generator
    models.py               # Pydantic data models
    scrapers/
      __init__.py
      linkedin.py           # LinkedIn job listings scraper
      linkedin_posts.py     # LinkedIn posts scraper (via Brave Search)
      twitter.py            # X/Twitter API v2 scraper
```

## Notes

- **No login required** — LinkedIn scraping uses public pages; posts are discovered via Brave Search.
- **X/Twitter free tier** — ~1,500 reads/month. Reacher handles rate limits gracefully.
- **Gmail App Passwords** — Requires 2FA on your Google Account. This is not your regular password.
- **Rate limits** — Default: 10 per run, 30 per day. Tune in `config.yaml`.
- **One per company** — Even if Reacher finds 5 roles at the same company, only the best match gets an email.

## License

MIT
