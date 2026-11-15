import threading
from datetime import date, timedelta

import pytest

from app import app as flask_app
from app import get_conn

# Helpers
def post_booking(client, body):
    return client.post("/api/v1/bookings", json=body)


def confirmed_count(trip_id, seat_no):
    """How many CONFIRMED bookings exist for this seat (asks the database directly)."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM bookings "
            "WHERE trip_id = %s AND seat_no = %s AND status = 'CONFIRMED'",
            (trip_id, seat_no),
        ).fetchone()[0]


# Health and readiness

def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_ready(client):
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ready"

# Routes, trips and the seat map

def test_routes_list(client):
    response = client.get("/api/v1/routes")
    assert response.status_code == 200
    assert len(response.get_json()["routes"]) == 3


def test_route_has_ordered_stops(client):
    body = client.get("/api/v1/routes/1").get_json()
    assert body["origin"] == "Chandigarh"
    assert [s["seq"] for s in body["stops"]] == [1, 2, 3, 4, 5]


def test_unknown_route_is_404(client):
    assert client.get("/api/v1/routes/99").status_code == 404


def test_trips_can_be_filtered_by_date(client, trip):
    url = f"/api/v1/trips?route_id=1&date={trip['departure_date']}"
    trips = client.get(url).get_json()["trips"]
    assert len(trips) == 2  # the 06:00 and the 18:00 departure
    assert all(t["departure_date"] == trip["departure_date"] for t in trips)
    assert all(t["route_id"] == 1 for t in trips)


def test_seat_map_starts_all_available(client, trip):
    body = client.get(f"/api/v1/trips/{trip['id']}/seats").get_json()
    assert body["total"] == 20
    assert body["available"] == 20


def test_seat_map_unknown_trip_is_404(client):
    assert client.get("/api/v1/trips/999999/seats").status_code == 404


def test_seat_map_shows_booked_seat(client, trip, make_payload):
    assert post_booking(client, make_payload("2C")).status_code == 201
    body = client.get(f"/api/v1/trips/{trip['id']}/seats").get_json()
    assert body["available"] == 19
    booked = [s["seat_no"] for s in body["seats"] if s["status"] == "BOOKED"]
    assert booked == ["2C"]

# Creating and reading bookings

def test_valid_booking_returns_ticket(client, make_payload):
    response = post_booking(client, make_payload("2B"))
    assert response.status_code == 201
    body = response.get_json()
    assert body["pnr"].startswith("BB")
    assert len(body["pnr"]) == 10
    assert body["status"] == "CONFIRMED"
    assert body["seat_no"] == "2B"
    assert body["passenger_name"] == "Asha Verma"
    assert body["fare"] == 500  # 250 km x 2


def test_partial_journey_fare(client, make_payload):
    body = post_booking(
        client, make_payload("4A", boarding_stop_id=2, drop_stop_id=4)
    ).get_json()
    assert body["fare"] == (175 - 45) * 2


def test_get_booking_by_pnr(client, make_payload):
    pnr = post_booking(client, make_payload("3A")).get_json()["pnr"]
    response = client.get(f"/api/v1/bookings/{pnr}")
    assert response.status_code == 200
    assert response.get_json()["pnr"] == pnr


def test_get_booking_accepts_lowercase_pnr(client, make_payload):
    pnr = post_booking(client, make_payload("3B")).get_json()["pnr"]
    assert client.get(f"/api/v1/bookings/{pnr.lower()}").status_code == 200


def test_unknown_pnr_is_404(client):
    assert client.get("/api/v1/bookings/BBNOPE0000").status_code == 404

# Validation: bad input must give 422

@pytest.mark.parametrize(
    "overrides",
    [
        {"seat_no": "ZZ"},
        {"travel_date": "31-12-2030"},  # wrong date format
        {"boarding_stop_id": 4, "drop_stop_id": 2},  # drop before boarding
        {"boarding_stop_id": 3, "drop_stop_id": 3},  # same stop
        {"drop_stop_id": 7},  # stop 7 belongs to another route
        {"trip_id": "abc"},  # not a number
        {"passenger": {"name": "Asha Verma", "age": 0, "phone": "9876543210"}},
        {"passenger": {"name": "Asha Verma", "age": 200, "phone": "9876543210"}},
        {"passenger": {"name": "Asha Verma", "age": "29", "phone": "9876543210"}},
        {"passenger": {"name": "12345", "age": 29, "phone": "9876543210"}},
        {"passenger": {"name": "Asha Verma", "age": 29, "phone": "12"}},
    ],
)
def test_invalid_input_gives_422(client, make_payload, overrides):
    response = post_booking(client, make_payload(**overrides))
    assert response.status_code == 422
    assert response.get_json()["error"] == "validation_error"


def test_past_date_is_rejected(client, make_payload):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    response = post_booking(client, make_payload(travel_date=yesterday))
    assert response.status_code == 422


def test_date_must_match_the_trip(client, trip, make_payload):
    next_day = (date.fromisoformat(trip["departure_date"]) + timedelta(days=1)).isoformat()
    response = post_booking(client, make_payload(travel_date=next_day))
    assert response.status_code == 422


def test_missing_passenger_is_rejected(client, make_payload):
    body = make_payload()
    del body["passenger"]
    assert post_booking(client, body).status_code == 422


def test_non_json_body_is_rejected(client):
    response = client.post("/api/v1/bookings", data="this is not json")
    assert response.status_code == 422


def test_unknown_trip_is_404(client, make_payload):
    assert post_booking(client, make_payload(trip_id=999999)).status_code == 404


def test_unknown_seat_is_404(client, make_payload):
    assert post_booking(client, make_payload("9Z")).status_code == 404

# The core rule: one confirmed booking per seat

def test_same_seat_twice_gives_409(client, trip, make_payload):
    first = post_booking(client, make_payload("1A"))
    second = post_booking(client, make_payload("1A"))
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.get_json()["error"] == "seat_unavailable"
    assert "pnr" not in second.get_json()  # no ticket was created
    assert confirmed_count(trip["id"], "1A") == 1  # exactly one row in the database


def test_same_seat_on_another_trip_is_allowed(client, make_payload):
    trips = client.get("/api/v1/trips?route_id=1").get_json()["trips"]
    other = trips[1]  # a different trip (the second departure of the day)
    on_other_trip = make_payload(
        "1A", trip_id=other["id"], travel_date=other["departure_date"]
    )
    assert post_booking(client, make_payload("1A")).status_code == 201
    assert post_booking(client, on_other_trip).status_code == 201

# Cancellation

def test_cancel_booking(client, make_payload):
    pnr = post_booking(client, make_payload("1B")).get_json()["pnr"]
    response = client.post(f"/api/v1/bookings/{pnr}/cancel")
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "CANCELLED"
    assert body["cancelled_at"] is not None


def test_cancel_twice_gives_409(client, make_payload):
    pnr = post_booking(client, make_payload("1C")).get_json()["pnr"]
    assert client.post(f"/api/v1/bookings/{pnr}/cancel").status_code == 200
    again = client.post(f"/api/v1/bookings/{pnr}/cancel")
    assert again.status_code == 409
    assert again.get_json()["error"] == "already_cancelled"


def test_cancel_unknown_pnr_is_404(client):
    assert client.post("/api/v1/bookings/BBNOPE0000/cancel").status_code == 404


def test_cancelled_seat_can_be_booked_again(client, trip, make_payload):
    first_pnr = post_booking(client, make_payload("1D")).get_json()["pnr"]
    client.post(f"/api/v1/bookings/{first_pnr}/cancel")
    again = post_booking(client, make_payload("1D"))
    assert again.status_code == 201
    assert again.get_json()["pnr"] != first_pnr  # a brand new PNR
    assert confirmed_count(trip["id"], "1D") == 1

# Concurrency: 20 requests at the same instant, one seat

def test_concurrent_requests_only_one_wins(trip, make_payload):
    results = []
    barrier = threading.Barrier(20)

    def worker(i):
        client = flask_app.test_client()  # each thread gets its own client
        barrier.wait()  # everyone waits here, then all fire together
        passenger = {"name": f"Rider {chr(65 + i)}", "age": 30, "phone": "9876543210"}
        response = client.post(
            "/api/v1/bookings", json=make_payload("5D", passenger=passenger)
        )
        results.append(response.status_code)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(201) == 1
    assert results.count(409) == 19
    assert confirmed_count(trip["id"], "5D") == 1