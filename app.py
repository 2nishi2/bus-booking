import os
 
import psycopg
from flask import Flask, jsonify
 
app = Flask(__name__)
 
 
def get_conn():
    url = os.environ.get("DATABASE_URL")  # used by tests and CI
    if url:
        return psycopg.connect(url, connect_timeout=3)
    pw_file = os.environ.get("DB_PASSWORD_FILE")
    password = open(pw_file).read().strip() if pw_file else os.environ.get("DB_PASSWORD", "")
    return psycopg.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        dbname=os.environ.get("DB_NAME", "bus_booking"),
        user=os.environ.get("DB_USER", "bus"),
        password=password,
        connect_timeout=3,
    )
 
 
@app.get("/health")
def health():
    # Liveness: is the program itself alive? Never touches the database.
    return jsonify(status="ok")
 
 
@app.get("/ready")
def ready():
    # Readiness: can we actually serve requests (database reachable)?
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return jsonify(status="ready")
    except psycopg.OperationalError:
        return jsonify(status="not_ready"), 503
