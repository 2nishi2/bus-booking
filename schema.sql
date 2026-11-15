CREATE TABLE IF NOT EXISTS routes (
    id INTEGER PRIMARY KEY,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stops (
    id INTEGER PRIMARY KEY,
    route_id INTEGER NOT NULL REFERENCES routes(id),
    name TEXT NOT NULL,
    seq INTEGER NOT NULL,
    km_from_origin INTEGER NOT NULL,
    UNIQUE (route_id, seq)
);
CREATE TABLE IF NOT EXISTS trips (
    id INTEGER PRIMARY KEY,
    route_id INTEGER NOT NULL REFERENCES routes(id),
    departure_date TEXT NOT NULL,
    departure_time TEXT NOT NULL,
    rate_per_km INTEGER NOT NULL,
    UNIQUE (route_id, departure_date, departure_time)
);
CREATE TABLE IF NOT EXISTS seats (
    trip_id INTEGER NOT NULL REFERENCES trips(id),
    seat_no TEXT NOT NULL,
    PRIMARY KEY (trip_id, seat_no)
);
CREATE TABLE IF NOT EXISTS bookings (
    pnr TEXT PRIMARY KEY,
    trip_id INTEGER NOT NULL REFERENCES trips(id),
    seat_no TEXT NOT NULL,
    passenger_name TEXT NOT NULL,
    passenger_age INTEGER NOT NULL,
    passenger_phone TEXT NOT NULL,
    boarding_stop_id INTEGER NOT NULL REFERENCES stops(id),
    drop_stop_id INTEGER NOT NULL REFERENCES stops(id),
    travel_date TEXT NOT NULL,
    fare INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('CONFIRMED', 'CANCELLED')),
    created_at TEXT NOT NULL,
    cancelled_at TEXT,
    FOREIGN KEY (trip_id, seat_no) REFERENCES seats (trip_id, seat_no)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_bookings_confirmed_seat
    ON bookings (trip_id, seat_no) WHERE status = 'CONFIRMED';
