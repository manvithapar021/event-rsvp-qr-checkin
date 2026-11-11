"""End-to-end tests for Event RSVP + QR Check-in, using Flask's test client.
Run:  python -m pytest tests/test_app.py -v
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Use a real temp file for the test database, set BEFORE importing app:
# Flask-SQLAlchemy reads the URI when the engine is first created, so
# changing the config after import does not switch databases.
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = "sqlite:///" + _tmp.name

import app as app_module  # noqa: E402

app_module.app.config["TESTING"] = True


def make_client():
    with app_module.app.app_context():
        app_module.db.drop_all()
        app_module.db.create_all()
    return app_module.app.test_client()


def create_event(client):
    resp = client.post("/events/new", data={
        "name": "Test Workshop", "date_time": "2026-12-12T10:00",
        "venue": "Test Hall", "capacity": "2", "description": "Test event",
    }, follow_redirects=True)
    assert resp.status_code == 200
    with app_module.app.app_context():
        event = app_module.Event.query.first()
    return event.id


def test_full_flow():
    client = make_client()
    event_id = create_event(client)

    # 1. RSVP works and returns a QR ticket.
    resp = client.post(f"/events/{event_id}/rsvp", data={
        "name": "Test User", "email": "test@example.com",
        "college": "SRMIST", "ticket_type": "Student",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"QR" in resp.data or b"Ticket" in resp.data
    with app_module.app.app_context():
        rsvp = app_module.RSVP.query.filter_by(email="test@example.com").first()
        assert rsvp is not None and rsvp.status == "confirmed"
        code = rsvp.qr_code
        assert os.path.exists(os.path.join(app_module.QR_DIR, f"{code}.png"))

    # 2. Duplicate email for the same event is rejected.
    resp = client.post(f"/events/{event_id}/rsvp", data={
        "name": "Test User", "email": "TEST@example.com",
        "college": "SRMIST", "ticket_type": "Student",
    }, follow_redirects=True)
    with app_module.app.app_context():
        assert app_module.RSVP.query.filter_by(event_id=event_id).count() == 1

    # 3. Invalid email is rejected.
    resp = client.post(f"/events/{event_id}/rsvp", data={
        "name": "Bad", "email": "not-an-email",
        "college": "", "ticket_type": "General",
    }, follow_redirects=True)
    with app_module.app.app_context():
        assert app_module.RSVP.query.filter_by(email="not-an-email").first() is None

    # 4. Capacity: 2nd RSVP confirms, 3rd goes to the waitlist.
    client.post(f"/events/{event_id}/rsvp", data={
        "name": "Second", "email": "second@example.com",
        "college": "SRMIST", "ticket_type": "General",
    })
    client.post(f"/events/{event_id}/rsvp", data={
        "name": "Third", "email": "third@example.com",
        "college": "SRMIST", "ticket_type": "General",
    })
    with app_module.app.app_context():
        third = app_module.RSVP.query.filter_by(email="third@example.com").first()
        assert third.status == "waitlist"

    # 5. Check-in succeeds once, then reports already-checked-in.
    resp = client.post("/checkin", data={"code": code}, follow_redirects=True)
    assert b"Checked in" in resp.data
    resp = client.post("/checkin", data={"code": code}, follow_redirects=True)
    assert b"already checked in" in resp.data
    resp = client.post("/checkin", data={"code": "NOPE12345678"}, follow_redirects=True)
    assert b"Invalid code" in resp.data

    # 6. Dashboard loads and shows the counts.
    resp = client.get(f"/events/{event_id}/dashboard")
    assert resp.status_code == 200
    assert b"Checked in" in resp.data

    # 7. Exports return a file.
    resp = client.get(f"/events/{event_id}/export.csv")
    assert resp.status_code == 200 and b"test@example.com" in resp.data
    resp = client.get(f"/events/{event_id}/export.xlsx")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith(
        "application/vnd.openxmlformats-officedocument")

    # 8. Attendee search / filter works.
    resp = client.get(f"/events/{event_id}/attendees?q=Second")
    assert resp.status_code == 200 and b"second@example.com" in resp.data
    resp = client.get(f"/events/{event_id}/attendees?status=waitlist")
    assert b"third@example.com" in resp.data
