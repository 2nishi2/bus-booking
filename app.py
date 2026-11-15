import os
import re
import secrets
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import psycopg
from flask import Flask, jsonify, request
from psycopg.rows import dict_row

app = Flask(__name__)

ROUTES = [
    (
        1,
        "Chandigarh",
        "Delhi",
        [
            ("Chandigarh", 0),
            ("Ambala", 45),
            ("Karnal", 125),
            ("Panipat", 175),
            ("Delhi", 250),
        ],
    ),
    (
        2,
        "Amritsar",
        "Chandigarh",
        [("Amritsar", 0), ("Jalandhar", 80), ("Ludhiana", 145), ("Chandigarh", 230)],
    ),
    (
        3,
        "Delhi",
        "Jaipur",
        [
            ("Delhi", 0),
            ("Gurugram", 30),
            ("Neemrana", 120),
            ("Kotputli", 180),
            ("Jaipur", 270),
        ],
    ),
]


def get_conn():
    url = os.environ.get("DATABASE_URL")  # used by tests and CI
    if url:
        return psycopg.connect(url, connect_timeout=3)
    pw_file = os.environ.get("DB_PASSWORD_FILE")
    if pw_file:
        password = Path(pw_file).read_text().strip()
    else:
        password = os.environ.get("DB_PASSWORD", "")
    return psycopg.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        dbname=os.environ.get("DB_NAME", "bus_booking"),
        user=os.environ.get("DB_USER", "bus"),
        password=password,
        connect_timeout=3,
    )


def init_db():
    with get_conn() as conn:
        # (a) create the tables
        schema = Path("schema.sql").read_text()
        for statement in schema.split(";"):
            if statement.strip():
                conn.execute(statement)

        # (b) seed only if empty
        count = conn.execute("SELECT COUNT(*) FROM routes").fetchone()[0]
        if count > 0:
            return

        stop_id = 1
        trip_id = 1
        for route_id, origin, destination, stops in ROUTES:
            conn.execute(
                "INSERT INTO routes (id, origin, destination) VALUES (%s, %s, %s)",
                (route_id, origin, destination),
            )
            for seq, (name, km) in enumerate(stops, start=1):
                conn.execute(
                    "INSERT INTO stops (id, route_id, name, seq, km_from_origin) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (stop_id, route_id, name, seq, km),
                )
                stop_id += 1

            for day in range(14):
                travel_date = (date.today() + timedelta(days=day)).isoformat()
                for departure in ("06:00", "18:00"):
                    conn.execute(
                        "INSERT INTO trips (id, route_id, departure_date, departure_time, rate_per_km) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (trip_id, route_id, travel_date, departure, 2),
                    )
                    for row in range(1, 6):
                        for letter in "ABCD":
                            conn.execute(
                                "INSERT INTO seats (trip_id, seat_no) VALUES (%s, %s)",
                                (trip_id, f"{row}{letter}"),
                            )
                    trip_id += 1


def query(conn, sql, params=()):
    """Run a SELECT and return a list of dictionaries."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def query_one(conn, sql, params=()):
    """Run a SELECT and return the first row (or None)."""
    rows = query(conn, sql, params)
    return rows[0] if rows else None


NAME_RE = re.compile(r"[A-Za-z][A-Za-z .'-]{0,79}")
PHONE_RE = re.compile(r"\+?[0-9]{10,13}")
SEAT_RE = re.compile(r"[1-9][0-9]?[A-Z]")


def is_int(value):
    # True/False count as numbers in Python, so exclude them explicitly
    return isinstance(value, int) and not isinstance(value, bool)


def validate_booking(data):
    """Check the shape of the request. Returns (clean_values, errors)."""
    if not isinstance(data, dict):
        return None, {"body": "JSON object required"}
    clean, errors = {}, {}

    if is_int(data.get("trip_id")) and data["trip_id"] > 0:
        clean["trip_id"] = data["trip_id"]
    else:
        errors["trip_id"] = "positive whole number required"

    seat = data.get("seat_no")
    if isinstance(seat, str) and SEAT_RE.fullmatch(seat.strip().upper()):
        clean["seat_no"] = seat.strip().upper()
    else:
        errors["seat_no"] = "a seat like '3B' is required"

    try:
        parsed = datetime.strptime(str(data.get("travel_date")), "%Y-%m-%d").date()
        if parsed < date.today():
            errors["travel_date"] = "travel date is in the past"
        else:
            clean["travel_date"] = parsed.isoformat()
    except ValueError:
        errors["travel_date"] = "must be a valid date as YYYY-MM-DD"

    for field in ("boarding_stop_id", "drop_stop_id"):
        value = data.get(field)
        if is_int(value) and value > 0:
            clean[field] = value
        else:
            errors[field] = "positive whole number required"

    passenger = data.get("passenger")
    if not isinstance(passenger, dict):
        errors["passenger"] = "an object with name, age and phone is required"
    else:
        name = passenger.get("name")
        if isinstance(name, str) and NAME_RE.fullmatch(name.strip()):
            clean["passenger_name"] = " ".join(name.split())
        else:
            errors["passenger.name"] = "1 to 80 letters (spaces . ' - allowed)"
        age = passenger.get("age")
        if is_int(age) and 1 <= age <= 120:
            clean["passenger_age"] = age
        else:
            errors["passenger.age"] = "whole number from 1 to 120"
        phone = passenger.get("phone")
        if isinstance(phone, str) and PHONE_RE.fullmatch(phone.strip()):
            clean["passenger_phone"] = phone.strip()
        else:
            errors["passenger.phone"] = "10 to 13 digits, optional leading +"

    return clean, errors


PNR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I, they look alike


def new_pnr():
    return "BB" + "".join(secrets.choice(PNR_ALPHABET) for _ in range(8))


@app.post("/api/v1/bookings")
def create_booking():
    data = request.get_json(silent=True)
    clean, errors = validate_booking(data)
    if errors:
        return jsonify(error="validation_error", details=errors), 422

    with get_conn() as conn:
        # --- stage 2: facts that need the database ---
        trip = query_one(
            conn,
            "SELECT id, route_id, departure_date, rate_per_km FROM trips WHERE id = %s",
            (clean["trip_id"],),
        )
        if trip is None:
            return jsonify(error="not_found", message="trip not found"), 404
        if clean["travel_date"] != trip["departure_date"]:
            return jsonify(
                error="validation_error",
                details={
                    "travel_date": f"this trip departs on {trip['departure_date']}"
                },
            ), 422

        stop_rows = query(
            conn,
            "SELECT id, seq, km_from_origin FROM stops WHERE route_id = %s",
            (trip["route_id"],),
        )

        stops = {s["id"]: s for s in stop_rows}
        stop_errors = {}
        for field in ("boarding_stop_id", "drop_stop_id"):
            if clean[field] not in stops:
                stop_errors[field] = "stop does not belong to this trip's route"
        if not stop_errors and (
            stops[clean["boarding_stop_id"]]["seq"]
            >= stops[clean["drop_stop_id"]]["seq"]
        ):
            stop_errors["drop_stop_id"] = "must come after the boarding stop"
        if stop_errors:
            return jsonify(error="validation_error", details=stop_errors), 422

        seat = query_one(
            conn,
            "SELECT seat_no FROM seats WHERE trip_id = %s AND seat_no = %s",
            (trip["id"], clean["seat_no"]),
        )
        if seat is None:
            return jsonify(
                error="not_found", message="seat does not exist on this trip"
            ), 404

        km = (
            stops[clean["drop_stop_id"]]["km_from_origin"]
            - stops[clean["boarding_stop_id"]]["km_from_origin"]
        )
        fare = km * trip["rate_per_km"]
        now = datetime.now(UTC).isoformat(timespec="seconds")

        # --- the safe insert ---
        for _ in range(5):
            pnr = new_pnr()
            try:
                conn.execute(
                    "INSERT INTO bookings (pnr, trip_id, seat_no, passenger_name, "
                    "passenger_age, passenger_phone, boarding_stop_id, drop_stop_id, "
                    "travel_date, fare, status, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'CONFIRMED', %s)",
                    (
                        pnr,
                        trip["id"],
                        clean["seat_no"],
                        clean["passenger_name"],
                        clean["passenger_age"],
                        clean["passenger_phone"],
                        clean["boarding_stop_id"],
                        clean["drop_stop_id"],
                        clean["travel_date"],
                        fare,
                        now,
                    ),
                )
                conn.commit()
            except psycopg.errors.UniqueViolation:
                conn.rollback()
                taken = query_one(
                    conn,
                    "SELECT pnr FROM bookings "
                    "WHERE trip_id = %s AND seat_no = %s AND status = 'CONFIRMED'",
                    (trip["id"], clean["seat_no"]),
                )
                if taken:
                    return jsonify(
                        error="seat_unavailable",
                        message="this seat is already booked for the trip",
                    ), 409
                continue  # the PNR itself collided (very rare): try a new one
            booking = query_one(conn, "SELECT * FROM bookings WHERE pnr = %s", (pnr,))
            return jsonify(booking), 201
        return jsonify(error="pnr_generation_failed"), 500


@app.get("/health")
def health():
    # Liveness: is the program itself alive? Never touches the database.
    return jsonify(status="ok", version=os.environ.get("APP_VERSION", "dev"))


@app.get("/ready")
def ready():
    # Readiness: can we actually serve requests (database reachable)?
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return jsonify(status="ready")
    except psycopg.OperationalError:
        return jsonify(status="not_ready"), 503


@app.get("/api/v1/routes")
def list_routes():
    with get_conn() as conn:
        rows = query(conn, "SELECT id, origin, destination FROM routes ORDER BY id")
    return jsonify(routes=rows)


@app.get("/api/v1/routes/<int:route_id>")
def get_route(route_id):
    with get_conn() as conn:
        route = query_one(
            conn,
            "SELECT id, origin, destination FROM routes WHERE id = %s",
            (route_id,),
        )
        if route is None:
            return jsonify(error="not_found"), 404
        route["stops"] = query(
            conn,
            "SELECT id, name, seq, km_from_origin FROM stops WHERE route_id = %s ORDER BY seq",
            (route_id,),
        )
    return jsonify(route)


@app.get("/api/v1/trips")
def list_trips():
    sql = (
        "SELECT id, route_id, departure_date, departure_time, rate_per_km "
        "FROM trips WHERE departure_date >= %s"
    )
    params = [date.today().isoformat()]

    route_id = request.args.get("route_id", type=int)
    if route_id:
        sql += " AND route_id = %s"
        params.append(route_id)

    travel_date = request.args.get("date")
    if travel_date:
        sql += " AND departure_date = %s"
        params.append(travel_date)

    sql += " ORDER BY departure_date, departure_time, id"
    with get_conn() as conn:
        rows = query(conn, sql, params)
    return jsonify(trips=rows)


@app.get("/api/v1/trips/<int:trip_id>/seats")
def seat_map(trip_id):
    with get_conn() as conn:
        trip = query_one(conn, "SELECT id FROM trips WHERE id = %s", (trip_id,))
        if trip is None:
            return jsonify(error="not_found"), 404
        seats = query(
            conn,
            "SELECT s.seat_no, "
            "CASE WHEN b.pnr IS NULL THEN 'AVAILABLE' ELSE 'BOOKED' END AS status "
            "FROM seats s "
            "LEFT JOIN bookings b ON b.trip_id = s.trip_id "
            "AND b.seat_no = s.seat_no AND b.status = 'CONFIRMED' "
            "WHERE s.trip_id = %s ORDER BY s.seat_no",
            (trip_id,),
        )
    available = sum(1 for s in seats if s["status"] == "AVAILABLE")
    return jsonify(trip_id=trip_id, available=available, total=len(seats), seats=seats)


@app.get("/api/v1/bookings/<pnr>")
def get_booking(pnr):
    with get_conn() as conn:
        row = query_one(conn, "SELECT * FROM bookings WHERE pnr = %s", (pnr.upper(),))
    if row is None:
        return jsonify(error="not_found"), 404
    return jsonify(row)


@app.post("/api/v1/bookings/<pnr>/cancel")
def cancel_booking(pnr):
    pnr = pnr.upper()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE bookings SET status = 'CANCELLED', cancelled_at = %s "
                "WHERE pnr = %s AND status = 'CONFIRMED'",
                (now, pnr),
            )
            changed = cur.rowcount
        row = query_one(conn, "SELECT * FROM bookings WHERE pnr = %s", (pnr,))
    if row is None:
        return jsonify(error="not_found"), 404
    if changed == 0:
        return jsonify(error="already_cancelled"), 409
    return jsonify(row)
