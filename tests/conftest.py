import os

import pytest

from app import app as flask_app
from app import get_conn, init_db


@pytest.fixture(scope="session", autouse=True)
def database():
    # Safety check: tests delete data, so they must never touch the real database.
    url = os.environ.get("DATABASE_URL", "")
    assert "bus_test" in url, "Set DATABASE_URL to the test database first"
    init_db()


@pytest.fixture(autouse=True)
def clean_bookings():
    # Start every test with no bookings, so tests don't affect each other.
    with get_conn() as conn:
        conn.execute("DELETE FROM bookings")
    yield


@pytest.fixture
def client():
    return flask_app.test_client()


@pytest.fixture
def trip(client):
    # The first upcoming trip on route 1 (stops 1 to 5)
    return client.get("/api/v1/trips?route_id=1").get_json()["trips"][0]


@pytest.fixture
def make_payload(trip):
    def _make(seat="1A", **overrides):
        body = {
            "trip_id": trip["id"],
            "seat_no": seat,
            "travel_date": trip["departure_date"],
            "boarding_stop_id": 1,
            "drop_stop_id": 5,
            "passenger": {"name": "Asha Verma", "age": 29, "phone": "9876543210"},
        }
        body.update(overrides)
        return body

    return _make