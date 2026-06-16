from flask import Flask, render_template, request, redirect, jsonify
import os
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")


def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            id SERIAL PRIMARY KEY,
            mnh VARCHAR(255) NOT NULL,
            device_type VARCHAR(100) NOT NULL,
            model VARCHAR(255),
            serial_number VARCHAR(255),
            computer_name VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    cur.close()
    conn.close()


@app.route("/")
def home():
    search = request.args.get("search", "").strip()

    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if search:
        cur.execute("""
            SELECT *
            FROM devices
            WHERE mnh ILIKE %s
               OR device_type ILIKE %s
               OR model ILIKE %s
               OR serial_number ILIKE %s
               OR computer_name ILIKE %s
            ORDER BY id DESC
        """, tuple([f"%{search}%"] * 5))
    else:
        cur.execute("""
            SELECT *
            FROM devices
            ORDER BY id DESC
        """)

    rows = cur.fetchall()

    cur.execute("""
        SELECT DISTINCT model
        FROM devices
        WHERE model IS NOT NULL AND model <> ''
        ORDER BY model
    """)
    models = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "index.html",
        rows=rows,
        models=models,
        search=search
    )


@app.route("/save", methods=["POST"])
def save():
    mnh = request.form.get("mnh", "").strip()
    device_type = request.form.get("device_type", "").strip()
    model = request.form.get("model", "").strip()
    serial_number = request.form.get("serial_number", "").strip()
    computer_name = request.form.get("computer_name", "").strip()

    if device_type == "PC":
        serial_number = mnh

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO devices (
            mnh,
            device_type,
            model,
            serial_number,
            computer_name
        )
        VALUES (%s, %s, %s, %s, %s)
    """, (
        mnh,
        device_type,
        model,
        serial_number,
        computer_name
    ))

    conn.commit()
    cur.close()
    conn.close()

    return redirect("/")


@app.route("/models")
def get_models():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT DISTINCT model
        FROM devices
        WHERE model IS NOT NULL AND model <> ''
        ORDER BY model
    """)

    data = [r[0] for r in cur.fetchall()]

    cur.close()
    conn.close()

    return jsonify(data)


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)