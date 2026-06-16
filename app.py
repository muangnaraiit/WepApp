from flask import Flask, render_template, request, redirect, jsonify
import os
import re
from urllib.parse import unquote
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
DATABASE_URL = os.getenv("DATABASE_URL")


def get_db():
    if not DATABASE_URL:
        raise Exception("ไม่พบค่า DATABASE_URL")
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def clean_mnh(value):
    if not value:
        return ""

    value = unquote(str(value).strip())

    prefix = "https://www.dsmetsmart.com/dsmet_hos/test_barcode.php?id="

    if value.lower().startswith(prefix.lower()):
        value = value[len(prefix):]

    value = value.upper().strip()

    if not re.match(r"^[A-Z0-9\-]+$", value):
        return ""

    return value


@app.route("/")
def home():
    search = clean_mnh(request.args.get("search", "").strip()) or request.args.get("search", "").strip()

    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    order_sql = """
        ORDER BY
            REGEXP_REPLACE(UPPER(mnh), '[0-9]', '', 'g'),
            NULLIF(REGEXP_REPLACE(mnh, '[^0-9]', '', 'g'), '')::INTEGER,
            mnh
    """

    if search:
        keyword = f"%{search}%"
        cur.execute(f"""
            SELECT *
            FROM devices
            WHERE mnh ILIKE %s
               OR device_type ILIKE %s
               OR model ILIKE %s
               OR serial_number ILIKE %s
               OR computer_name ILIKE %s
            {order_sql}
        """, (keyword, keyword, keyword, keyword, keyword))
    else:
        cur.execute(f"""
            SELECT *
            FROM devices
            {order_sql}
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

    return render_template("index.html", rows=rows, models=models, search=search)


@app.route("/api/device/<path:mnh>")
def api_device(mnh):
    mnh = clean_mnh(mnh)

    if not mnh:
        return jsonify({"found": False})

    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT *
        FROM devices
        WHERE mnh = %s
        LIMIT 1
    """, (mnh,))

    row = cur.fetchone()

    cur.close()
    conn.close()

    if row:
        return jsonify({"found": True, "data": row})

    return jsonify({"found": False})


@app.route("/save", methods=["POST"])
def save():
    mnh = clean_mnh(request.form.get("mnh", ""))
    device_type = request.form.get("device_type", "").strip()
    model = request.form.get("model", "").strip()
    serial_number = request.form.get("serial_number", "").strip()
    computer_name = request.form.get("computer_name", "").strip()

    if not mnh:
        return "MNH ไม่ถูกต้อง กรุณากรอกใหม่", 400

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
        ON CONFLICT (mnh)
        DO UPDATE SET
            device_type = EXCLUDED.device_type,
            model = EXCLUDED.model,
            serial_number = EXCLUDED.serial_number,
            computer_name = EXCLUDED.computer_name
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

    return redirect(f"/?search={mnh}")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)