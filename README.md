# Event RSVP + QR Check-in 🎟️

A Flask web app that replaces paper sign-in sheets at college fests and workshops.

## The problem — and why this exists

At a tech fest like Aaruush, check-in usually means a printed list, a pen, and a queue. Names get misspelt, people queue twice, and at the end nobody knows how many people actually showed up, or when.

This app fixes that: attendees RSVP online and get a unique QR ticket, staff scan the code at the door, and organisers get a live dashboard and a clean attendee spreadsheet — no paper, no duplicate check-ins, no guessing.

I built it as a 3rd-year CSE student at SRMIST, where I serve on the PR committee for Aaruush, so the workflow is based on how fest events actually run.

## Features

- **Create / manage events** — name, date/time, venue, capacity, description
- **Public RSVP form** — name, email, college, ticket type (General / Student / VIP), with validation
  - One email = one RSVP per event (checked case-insensitively)
  - **Waitlist:** when an event is full, extra RSVPs join the waitlist automatically
- **Unique QR code per RSVP** — generated as a PNG ticket, viewable and downloadable
- **Check-in desk** — type or scan a QR code; a code works once only
  - Clear states: checked in ✅, already checked in ⚠️, waitlisted ⚠️, invalid ❌
  - QR scanners can open `/checkin?code=XXXX` directly
- **Admin dashboard** — total RSVPs, confirmed, waitlist, checked-in, no-shows, check-in rate, plus Chart.js charts for breakdown by ticket type and by check-in hour (stats computed with **Pandas**)
- **Attendee list** — search by name/email/college, filter by status and ticket type
- **Export to CSV and Excel (.xlsx)** — the export is a clean, tidy table, so it opens directly in Excel and is **Power BI-ready** (import the .xlsx / .csv as a data source; no cleaning needed)
- **Seed data** — on first run it creates a realistic Aaruush workshop (18 RSVPs, 10 checked in) and a tiny 5-seat robotics demo that is already full, so the waitlist and dashboard are visible immediately

## Tech stack

Python · Flask · SQLAlchemy (SQLite) · Pandas · openpyxl · qrcode · Bootstrap 5 · Chart.js

No React, no Node, no ML — deliberately. Every line is explainable in an interview.

## Setup & run

```bash
# 1. Create a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run
python app.py

# 4. Open http://127.0.0.1:5000
```

On first run, seed data is created automatically. The SQLite database is stored in `instance/events.db` and QR-code images in `static/qr/` — both are git-ignored.

## Try it

1. Open the home page — you'll see the seeded Aaruush workshop.
2. RSVP for it with your own email, and open your QR ticket.
3. Go to **Check-in Desk**, enter the code from your ticket — it checks you in. Enter it again: you'll get "already checked in".
4. Open the **Dashboard** for the event to see the counts and charts.
5. Try the 5-seat **Robotics Demo** — RSVP with a new email and you'll join the waitlist, because it's full.
6. Export the attendee list as **Excel** from the dashboard, and open it in Excel or Power BI.

## Tests

```bash
pip install pytest
python -m pytest tests/test_app.py -v
```

The test covers the full flow: create event → RSVP → duplicate-email rejection → invalid-email rejection → capacity / waitlist → check-in once → already-checked-in → invalid code → dashboard → CSV & Excel export → attendee search/filter.

## Project structure

```
event-rsvp-qr-checkin/
├── app.py              # Flask app: models, routes, Pandas stats, QR codes, seed data
├── requirements.txt
├── README.md
├── .gitignore
├── static/
│   ├── css/style.css
│   └── qr/             # generated QR-code PNGs (git-ignored)
├── templates/          # Jinja2 + Bootstrap pages
│   ├── base.html  index.html  create_event.html
│   ├── event_detail.html  ticket.html  checkin.html
│   └── dashboard.html  attendees.html
└── tests/
    └── test_app.py     # end-to-end test of the main flows
```

## Interview talking points

1. **Capacity and the waitlist are a real business rule, not just CRUD.** `confirmed_count` is compared with `capacity` at RSVP time; once full, new RSVPs get `status="waitlist"` and no usable ticket. I can explain why that check has to happen server-side, not just in the UI.
2. **A QR code must work exactly once.** Check-in sets `checked_in=True` and records `checked_in_at`; a second scan hits the already-checked-in branch. I chose a short random code (`uuid4().hex[:12]`) stored in the database, rather than trusting anything the client sends.
3. **Duplicate prevention is case-insensitive.** `TEST@example.com` and `test@example.com` are the same person — the duplicate check uses `lower(email)`, which is a bug I would have missed if I'd only compared the raw strings.
4. **Pandas does the dashboard maths.** Instead of hand-written loops, `value_counts()` gives the ticket-type breakdown and check-in-hour breakdown in one line each, and the same DataFrame powers the CSV/Excel export — so the dashboard and the export can never disagree.

## What I'd improve next

- Email the QR ticket to the attendee on RSVP (currently shown on screen / downloadable)
- Promote waitlisted people automatically when a confirmed RSVP cancels
- A simple organiser login, so only organisers see the dashboard and attendee list
