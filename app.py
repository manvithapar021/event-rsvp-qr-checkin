"""
Event RSVP + QR Check-in
========================
A Flask app for managing event registrations without paper lists.

What it does
------------
1. Organisers create events (name, date/time, venue, capacity).
2. Attendees RSVP on a public form. One email = one RSVP per event.
   When an event is full, extra RSVPs join a waitlist automatically.
3. Every confirmed RSVP gets a unique QR code (a PNG ticket).
4. At the door, staff enter / scan the QR code to check someone in.
   A code can only be used once.
5. The dashboard shows live stats (built with Pandas):
   total RSVPs, checked-in, no-shows, check-in rate, and breakdowns
   by ticket type and by check-in hour.
6. Attendees can be exported to CSV and Excel (.xlsx), so the
   list is ready to open in Excel or Power BI.

Stack: Python, Flask, SQLAlchemy (SQLite), Pandas, qrcode, Bootstrap.
No React, no Node, no ML — deliberately, so every line is explainable.

Run it:
    pip install -r requirements.txt
    python app.py
    # open http://127.0.0.1:5000
"""

import os
import re
import uuid
from datetime import datetime, timedelta

import pandas as pd
import qrcode
from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QR_DIR = os.path.join(BASE_DIR, "static", "qr")
os.makedirs(QR_DIR, exist_ok=True)
# SQLite cannot create the file if its folder does not exist yet.
os.makedirs(os.path.join(BASE_DIR, "instance"), exist_ok=True)

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-only-change-me"  # change in production
# Tests set DATABASE_URL before importing this module; otherwise use
# the normal file database. (Changing the config after import does
# not change an already-created engine, so the env var is the hook.)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL",
    "sqlite:///" + os.path.join(BASE_DIR, "instance", "events.db"),
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# A simple email check: "something@something.something". Good enough for
# a form, and much clearer to explain than a giant regex.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TICKET_TYPES = ["General", "Student", "VIP"]


# ---------------------------------------------------------------------------
# Database models
# ---------------------------------------------------------------------------
class Event(db.Model):
    """One event, e.g. a workshop at a tech fest."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    date_time = db.Column(db.DateTime, nullable=False)
    venue = db.Column(db.String(200), nullable=False)
    capacity = db.Column(db.Integer, nullable=False, default=50)
    description = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # One event has many RSVPs. Deleting the event deletes its RSVPs.
    rsvps = db.relationship(
        "RSVP", backref="event", cascade="all, delete-orphan", lazy=True
    )

    @property
    def confirmed_count(self):
        return sum(1 for r in self.rsvps if r.status == "confirmed")

    @property
    def waitlist_count(self):
        return sum(1 for r in self.rsvps if r.status == "waitlist")

    @property
    def checked_in_count(self):
        return sum(1 for r in self.rsvps if r.checked_in)

    @property
    def is_full(self):
        return self.confirmed_count >= self.capacity


class RSVP(db.Model):
    """One person's registration for one event."""

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200), nullable=False)
    college = db.Column(db.String(200), default="")
    ticket_type = db.Column(db.String(50), default="General")
    # confirmed = has a seat, waitlist = event was full when they RSVP'd
    status = db.Column(db.String(20), default="confirmed")
    qr_code = db.Column(db.String(32), unique=True, nullable=False)
    checked_in = db.Column(db.Boolean, default=False)
    checked_in_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# QR codes
# ---------------------------------------------------------------------------
def make_qr_code(code):
    """Create a QR-code PNG for `code` and return its filename.

    The QR image just contains the short code (e.g. "A1B2C3D4E5F6").
    At the door, scanning it opens /checkin?code=<code>.
    """
    path = os.path.join(QR_DIR, f"{code}.png")
    if not os.path.exists(path):
        img = qrcode.make(code)
        img.save(path)
    return f"{code}.png"


def new_qr_code():
    """A short, unique code for a new RSVP."""
    while True:
        code = uuid.uuid4().hex[:12].upper()
        if not RSVP.query.filter_by(qr_code=code).first():
            return code


# ---------------------------------------------------------------------------
# Stats (Pandas)
# ---------------------------------------------------------------------------
def event_stats(event):
    """Return dashboard numbers for an event, computed with Pandas.

    Returns a dict the dashboard template and Chart.js can use.
    Keeping the maths in Pandas (instead of hand-written loops) is
    the point: groupby / value_counts do the breakdowns in one line.
    """
    rows = [
        {
            "name": r.name,
            "email": r.email,
            "college": r.college,
            "ticket_type": r.ticket_type,
            "status": r.status,
            "checked_in": r.checked_in,
            "checked_in_at": r.checked_in_at,
        }
        for r in event.rsvps
    ]
    # An empty event still needs a dashboard, so handle it early.
    if not rows:
        return {
            "total": 0,
            "confirmed": 0,
            "waitlist": 0,
            "checked_in": 0,
            "no_show": 0,
            "check_in_rate": 0.0,
            "by_ticket": {},
            "by_hour": {},
        }

    df = pd.DataFrame(rows)
    confirmed = int((df["status"] == "confirmed").sum())
    checked_in = int(df["checked_in"].sum())
    # No-show = confirmed a seat but never checked in.
    no_show = confirmed - checked_in
    check_in_rate = round(100 * checked_in / confirmed, 1) if confirmed else 0.0

    # Breakdown by ticket type, e.g. {"General": 10, "Student": 6, "VIP": 2}
    by_ticket = df["ticket_type"].value_counts().to_dict()
    # Breakdown by check-in hour, e.g. {"10:00": 4, "11:00": 6}
    # Only checked-in rows have a time, so drop the rest first.
    checked = df[df["checked_in_at"].notna()].copy()
    if not checked.empty:
        checked["hour"] = pd.to_datetime(checked["checked_in_at"]).dt.strftime("%H:00")
        by_hour = checked["hour"].value_counts().sort_index().to_dict()
    else:
        by_hour = {}

    return {
        "total": len(df),
        "confirmed": confirmed,
        "waitlist": int((df["status"] == "waitlist").sum()),
        "checked_in": checked_in,
        "no_show": no_show,
        "check_in_rate": check_in_rate,
        "by_ticket": {str(k): int(v) for k, v in by_ticket.items()},
        "by_hour": {str(k): int(v) for k, v in by_hour.items()},
    }


def attendees_frame(event):
    """All RSVPs for an event as a Pandas DataFrame (for export)."""
    rows = [
        {
            "Name": r.name,
            "Email": r.email,
            "College": r.college,
            "Ticket Type": r.ticket_type,
            "Status": r.status,
            "Checked In": "Yes" if r.checked_in else "No",
            "Check-in Time": r.checked_in_at.strftime("%Y-%m-%d %H:%M")
            if r.checked_in_at
            else "",
            "QR Code": r.qr_code,
            "Registered At": r.created_at.strftime("%Y-%m-%d %H:%M"),
        }
        for r in event.rsvps
    ]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    """Home page: list of events, newest first."""
    events = Event.query.order_by(Event.date_time.asc()).all()
    return render_template("index.html", events=events)


@app.route("/events/new", methods=["GET", "POST"])
def create_event():
    """Create an event. Simple server-side validation, then save."""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        venue = request.form.get("venue", "").strip()
        description = request.form.get("description", "").strip()
        try:
            capacity = int(request.form.get("capacity", "50"))
            date_time = datetime.fromisoformat(request.form.get("date_time", ""))
        except (ValueError, TypeError):
            flash("Please enter a valid date/time and capacity.", "danger")
            return render_template("create_event.html")
        if not name or not venue or capacity < 1:
            flash("Name, venue and a capacity of at least 1 are required.", "danger")
            return render_template("create_event.html")

        event = Event(
            name=name, date_time=date_time, venue=venue,
            capacity=capacity, description=description,
        )
        db.session.add(event)
        db.session.commit()
        flash(f"Event '{name}' created.", "success")
        return redirect(url_for("event_detail", event_id=event.id))
    return render_template("create_event.html")


@app.route("/events/<int:event_id>")
def event_detail(event_id):
    """Event page: details + the public RSVP form."""
    event = Event.query.get_or_404(event_id)
    return render_template(
        "event_detail.html", event=event, ticket_types=TICKET_TYPES
    )


@app.route("/events/<int:event_id>/rsvp", methods=["POST"])
def rsvp(event_id):
    """Handle an RSVP.

    Validation, in order:
    1. Name and a valid email are required.
    2. The same email cannot RSVP twice for the same event
       (compared case-insensitively, so A@x.com == a@x.com).
    3. If the event is full, the RSVP joins the waitlist instead
       of getting a seat — and gets no QR ticket yet.
    """
    event = Event.query.get_or_404(event_id)
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    college = request.form.get("college", "").strip()
    ticket_type = request.form.get("ticket_type", "General")
    if ticket_type not in TICKET_TYPES:
        ticket_type = "General"

    if not name or not EMAIL_RE.match(email):
        flash("Please enter your name and a valid email address.", "danger")
        return redirect(url_for("event_detail", event_id=event.id))

    duplicate = RSVP.query.filter(
        RSVP.event_id == event.id,
        db.func.lower(RSVP.email) == email,
    ).first()
    if duplicate:
        flash(
            "That email has already RSVP'd for this event. "
            "Check your ticket instead.",
            "warning",
        )
        return redirect(url_for("ticket", qr_code=duplicate.qr_code))

    status = "waitlist" if event.is_full else "confirmed"
    code = new_qr_code()
    rsvp = RSVP(
        event_id=event.id, name=name, email=email, college=college,
        ticket_type=ticket_type, status=status, qr_code=code,
    )
    db.session.add(rsvp)
    db.session.commit()

    if status == "waitlist":
        # Waitlisted people have no seat, so no QR ticket yet.
        make_qr_code(code)
        flash(
            "This event is full, so you've joined the waitlist. "
            "We'll email you if a seat opens up.",
            "info",
        )
        return redirect(url_for("event_detail", event_id=event.id))

    make_qr_code(code)
    flash("RSVP confirmed! Here is your QR ticket.", "success")
    return redirect(url_for("ticket", qr_code=code))


@app.route("/ticket/<qr_code>")
def ticket(qr_code):
    """Show one person's QR ticket, with a download link for the PNG."""
    rsvp = RSVP.query.filter_by(qr_code=qr_code).first_or_404()
    return render_template("ticket.html", rsvp=rsvp, event=rsvp.event)


@app.route("/ticket/<qr_code>/download")
def download_ticket(qr_code):
    """Download the QR-code PNG for a ticket."""
    rsvp = RSVP.query.filter_by(qr_code=qr_code).first_or_404()
    path = os.path.join(QR_DIR, f"{rsvp.qr_code}.png")
    return send_file(path, mimetype="image/png", as_attachment=True,
                     download_name=f"ticket-{rsvp.qr_code}.png")


@app.route("/checkin", methods=["GET", "POST"])
def checkin():
    """Door check-in page.

    Staff type (or scan) a QR code. Four outcomes, each shown clearly:
    - invalid code   -> red "not found" message
    - already checked in -> amber message, with the first check-in time
    - waitlisted     -> amber message, no seat to check in to
    - success        -> green message, and the person is marked checked-in
    A code can only succeed once, because `checked_in` is now True.
    """
    result = None
    if request.method == "POST":
        code = request.form.get("code", "").strip().upper()
        rsvp = RSVP.query.filter_by(qr_code=code).first()
        if not rsvp:
            result = ("danger", "Invalid code — no ticket found for that QR code.")
        elif rsvp.status == "waitlist":
            result = ("warning",
                      f"{rsvp.name} is on the waitlist for "
                      f"'{rsvp.event.name}' and has no seat to check in to.")
        elif rsvp.checked_in:
            when = rsvp.checked_in_at.strftime("%H:%M") if rsvp.checked_in_at else "earlier"
            result = ("warning",
                      f"{rsvp.name} was already checked in at {when}. "
                      "This ticket cannot be used twice.")
        else:
            rsvp.checked_in = True
            rsvp.checked_in_at = datetime.utcnow()
            db.session.commit()
            result = ("success",
                      f"Checked in: {rsvp.name} ({rsvp.ticket_type}) — "
                      f"'{rsvp.event.name}'. Welcome!")
        return render_template("checkin.html", result=result, code=code)

    # GET: allow /checkin?code=XXXX so a QR scanner can open this page.
    code = request.args.get("code", "").strip().upper()
    if code:
        rsvp = RSVP.query.filter_by(qr_code=code).first()
        if rsvp and rsvp.status == "confirmed" and not rsvp.checked_in:
            rsvp.checked_in = True
            rsvp.checked_in_at = datetime.utcnow()
            db.session.commit()
            result = ("success",
                      f"Checked in: {rsvp.name} ({rsvp.ticket_type}) — "
                      f"'{rsvp.event.name}'. Welcome!")
        elif rsvp and rsvp.checked_in:
            result = ("warning", f"{rsvp.name} was already checked in.")
        elif rsvp:
            result = ("warning", f"{rsvp.name} is on the waitlist.")
        else:
            result = ("danger", "Invalid code — no ticket found.")
    return render_template("checkin.html", result=result, code=code)


@app.route("/events/<int:event_id>/dashboard")
def dashboard(event_id):
    """Admin dashboard: Pandas stats + Chart.js charts."""
    event = Event.query.get_or_404(event_id)
    stats = event_stats(event)
    return render_template("dashboard.html", event=event, stats=stats)


@app.route("/events/<int:event_id>/attendees")
def attendees(event_id):
    """Attendee list, with search and filters."""
    event = Event.query.get_or_404(event_id)
    query = RSVP.query.filter_by(event_id=event.id)
    search = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    ticket = request.args.get("ticket", "")
    if search:
        like = f"%{search}%"
        query = query.filter(
            (RSVP.name.ilike(like)) | (RSVP.email.ilike(like))
            | (RSVP.college.ilike(like))
        )
    if status in ("confirmed", "waitlist"):
        query = query.filter_by(status=status)
    if ticket in TICKET_TYPES:
        query = query.filter_by(ticket_type=ticket)
    rows = query.order_by(RSVP.created_at.desc()).all()
    return render_template(
        "attendees.html", event=event, rows=rows,
        search=search, status=status, ticket=ticket,
        ticket_types=TICKET_TYPES,
    )


@app.route("/events/<int:event_id>/export.csv")
def export_csv(event_id):
    """Download attendees as CSV."""
    event = Event.query.get_or_404(event_id)
    path = os.path.join(BASE_DIR, f"attendees-{event.id}.csv")
    attendees_frame(event).to_csv(path, index=False)
    return send_file(path, mimetype="text/csv", as_attachment=True,
                     download_name=f"attendees-{event.id}.csv")


@app.route("/events/<int:event_id>/export.xlsx")
def export_xlsx(event_id):
    """Download attendees as Excel (.xlsx), ready for Excel / Power BI."""
    event = Event.query.get_or_404(event_id)
    path = os.path.join(BASE_DIR, f"attendees-{event.id}.xlsx")
    attendees_frame(event).to_excel(path, index=False, engine="openpyxl")
    return send_file(
        path,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True, download_name=f"attendees-{event.id}.xlsx",
    )


# ---------------------------------------------------------------------------
# Seed data — a realistic Aaruush-style tech-fest workshop
# ---------------------------------------------------------------------------
def seed_data():
    """Create demo events the first time the app runs, so the
    dashboard is not empty. Safe to run again: it does nothing
    if any event already exists."""
    if Event.query.first():
        return

    workshop = Event(
        name="AI & Machine Learning Workshop — Aaruush 2026",
        date_time=datetime(2026, 12, 12, 10, 0),
        venue="TP Ganesan Auditorium, SRMIST Kattankulathur",
        capacity=40,
        description=(
            "A hands-on workshop on Python, Pandas and building your "
            "first ML model, run as part of Aaruush, the national-level "
            "techno-management fest at SRMIST."
        ),
    )
    db.session.add(workshop)
    db.session.commit()

    # (name, email, college, ticket type, checked in?, hours ago checked in)
    people = [
        ("Aarav Sharma", "aarav.sharma@example.com", "SRMIST", "Student", True, 2),
        ("Priya Nair", "priya.nair@example.com", "SRMIST", "Student", True, 2),
        ("Rohan Mehta", "rohan.mehta@example.com", "VIT Vellore", "General", True, 1),
        ("Ananya Iyer", "ananya.iyer@example.com", "SRMIST", "Student", True, 1),
        ("Kavya Reddy", "kavya.reddy@example.com", "Anna University", "General", True, 3),
        ("Arjun Patel", "arjun.patel@example.com", "SRMIST", "Student", True, 3),
        ("Sneha Gupta", "sneha.gupta@example.com", "SRMIST", "VIP", True, 2),
        ("Vikram Singh", "vikram.singh@example.com", "IIT Madras", "General", True, 1),
        ("Divya Menon", "divya.menon@example.com", "SRMIST", "Student", True, 4),
        ("Aditya Rao", "aditya.rao@example.com", "SSN College", "General", True, 4),
        ("Meera Joshi", "meera.joshi@example.com", "SRMIST", "Student", False, 0),
        ("Nikhil Verma", "nikhil.verma@example.com", "SRMIST", "Student", False, 0),
        ("Ishita Das", "ishita.das@example.com", "VIT Vellore", "General", False, 0),
        ("Karan Malhotra", "karan.malhotra@example.com", "SRMIST", "Student", False, 0),
        ("Riya Kapoor", "riya.kapoor@example.com", "Delhi University", "General", False, 0),
        ("Sana Sheikh", "sana.sheikh@example.com", "SRMIST", "Student", False, 0),
        ("Dev Patel", "dev.patel@example.com", "SRMIST", "Student", False, 0),
        ("Tara Nair", "tara.nair@example.com", "Anna University", "General", False, 0),
    ]
    now = datetime.utcnow()
    for name, email, college, ticket, checked, hours_ago in people:
        code = new_qr_code()
        rsvp = RSVP(
            event_id=workshop.id, name=name, email=email, college=college,
            ticket_type=ticket, status="confirmed", qr_code=code,
            checked_in=checked,
            checked_in_at=(now - timedelta(hours=hours_ago)) if checked else None,
        )
        db.session.add(rsvp)
        make_qr_code(code)
    db.session.commit()

    # A second, tiny event that is already full — to demo the waitlist.
    demo = Event(
        name="Robotics Demo — Aaruush 2026",
        date_time=datetime(2026, 12, 13, 14, 0),
        venue="Mechanical Block, SRMIST",
        capacity=5,
        description="A 5-seat live demo of line-following robots built by SRM students.",
    )
    db.session.add(demo)
    db.session.commit()
    for i, (name, email) in enumerate([
        ("Aarav Sharma", "aarav2@example.com"),
        ("Priya Nair", "priya2@example.com"),
        ("Rohan Mehta", "rohan2@example.com"),
        ("Ananya Iyer", "ananya2@example.com"),
        ("Kavya Reddy", "kavya2@example.com"),
        ("Arjun Patel", "arjun2@example.com"),   # waitlist
        ("Sneha Gupta", "sneha2@example.com"),   # waitlist
    ]):
        code = new_qr_code()
        rsvp = RSVP(
            event_id=demo.id, name=name, email=email, college="SRMIST",
            ticket_type="General",
            status="confirmed" if i < 5 else "waitlist",
            qr_code=code,
        )
        db.session.add(rsvp)
        make_qr_code(code)
    db.session.commit()


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        seed_data()
    # debug=True gives a reloader and a helpful error page while building.
    app.run(debug=True)
