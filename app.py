from flask import Flask, render_template, request, redirect, jsonify, send_file
import os
import re
from io import BytesIO
from urllib.parse import unquote, urlencode
import psycopg2
from psycopg2.extras import RealDictCursor
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


app = Flask(__name__)
DATABASE_URL = os.getenv("DATABASE_URL")


def get_db():
    if not DATABASE_URL:
        raise Exception("ไม่พบค่า DATABASE_URL")
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def ensure_device_extra_columns():
    """เพิ่มคอลัมน์ใหม่ให้อัตโนมัติ กรณีฐานข้อมูลเดิมยังไม่มีฟิลด์ประกัน/IP"""
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        ALTER TABLE devices
        ADD COLUMN IF NOT EXISTS ip_address VARCHAR(50),
        ADD COLUMN IF NOT EXISTS warranty_start DATE,
        ADD COLUMN IF NOT EXISTS warranty_end DATE
    """)

    conn.commit()
    cur.close()
    conn.close()


def format_device_row(row):
    """แปลงข้อมูล date ให้เป็น yyyy-mm-dd เพื่อใช้กับ input type=date และ JSON"""
    if not row:
        return None

    data = dict(row)

    for key in ("warranty_start", "warranty_end"):
        value = data.get(key)
        if hasattr(value, "isoformat"):
            data[key] = value.isoformat()
        elif value is None:
            data[key] = ""

    return data


def get_search_value():
    """รับค่าค้นหาจากทั้ง query string และ form เพื่อให้ค้างค่าหลังบันทึก/อัปเดต/ลบ"""
    return (
        request.form.get("search", "")
        or request.args.get("search", "")
        or ""
    ).strip()


def redirect_home_with_search(search_value):
    """กลับหน้าแรกพร้อมคงค่าค้นหาเดิมไว้ ถ้ามีค่า"""
    search_value = (search_value or "").strip()

    if search_value:
        return redirect("/?" + urlencode({"search": search_value}))

    return redirect("/")


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


def excel_text(value):
    """
    แปลงค่าทุกช่องให้เป็น Text สำหรับ Excel
    - None เป็นค่าว่าง
    - ค่าอื่นแปลงเป็น string
    - กัน Excel ตีความเป็นสูตร ถ้าขึ้นต้นด้วย = + - @
    """
    if value is None:
        return ""

    value = str(value)

    if value.startswith(("=", "+", "-", "@")):
        return "'" + value

    return value


ORDER_SQL = """
    ORDER BY
        computer_name ASC NULLS LAST,

        CASE device_type
            WHEN 'PC' THEN 1
            WHEN 'AIO' THEN 2
            WHEN 'Notebook' THEN 3
            WHEN 'Monitor' THEN 4
            WHEN 'Printer' THEN 5
            WHEN 'Scanner' THEN 6
            WHEN 'UPS' THEN 7
            WHEN 'Phone' THEN 8
            WHEN 'Switch Hub' THEN 9
            WHEN 'Projector' THEN 10
            ELSE 99
        END ASC,

        model ASC NULLS LAST,
        serial_number ASC NULLS LAST,
        mnh ASC NULLS LAST
"""


@app.route("/")
def home():
    ensure_device_extra_columns()

    search_raw = request.args.get("search", "").strip()
    search = clean_mnh(search_raw) or search_raw

    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

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
               OR ip_address ILIKE %s
               OR TO_CHAR(warranty_start, 'YYYY-MM-DD') ILIKE %s
               OR TO_CHAR(warranty_end, 'YYYY-MM-DD') ILIKE %s
            {ORDER_SQL}
        """, (keyword, keyword, keyword, keyword, keyword, keyword, keyword, keyword))
    else:
        cur.execute(f"""
            SELECT *
            FROM devices
            {ORDER_SQL}
        """)

    rows = cur.fetchall()

    cur.execute("""
        SELECT DISTINCT model
        FROM devices
        WHERE model IS NOT NULL
          AND model <> ''
        ORDER BY model
    """)
    models = cur.fetchall()

    cur.execute("""
        SELECT DISTINCT computer_name
        FROM devices
        WHERE computer_name IS NOT NULL
          AND computer_name <> ''
        ORDER BY computer_name
    """)
    computer_names = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "index.html",
        rows=rows,
        models=models,
        computer_names=computer_names,
        search=search
    )


@app.route("/api/device/<path:mnh>")
def api_device(mnh):
    ensure_device_extra_columns()

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
        return jsonify({"found": True, "data": format_device_row(row)})

    return jsonify({"found": False})


@app.route("/save", methods=["POST"])
def save():
    ensure_device_extra_columns()

    current_search = get_search_value()

    edit_id_raw = request.form.get("edit_id", "").strip()
    original_mnh = clean_mnh(request.form.get("original_mnh", ""))

    mnh = clean_mnh(request.form.get("mnh", ""))
    device_type = request.form.get("device_type", "").strip()
    model = request.form.get("model", "").strip()
    serial_number = request.form.get("serial_number", "").strip()
    computer_name = request.form.get("computer_name", "").strip()
    ip_address = request.form.get("ip_address", "").strip()
    warranty_start = request.form.get("warranty_start") or None
    warranty_end = request.form.get("warranty_end") or None

    if not mnh:
        return "MNH ไม่ถูกต้อง กรุณากรอกใหม่", 400

    if device_type == "PC":
        model = "PC"
        serial_number = "PC"

    edit_id = None
    if edit_id_raw.isdigit():
        edit_id = int(edit_id_raw)

    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    try:
        if edit_id:
            # โหมดแก้ไข:
            # 1) ถ้า MNH ที่พิมพ์ไปตรงกับข้อมูลตัวอื่น ระบบจะอัปเดตตัวนั้น
            # 2) ถ้า MNH ที่พิมพ์ไม่ตรงกับข้อมูลที่มีในระบบ ระบบจะอัปเดตแถวเดิมและเปลี่ยน MNH ของแถวเดิม
            cur.execute("""
                SELECT id
                FROM devices
                WHERE mnh = %s
                LIMIT 1
            """, (mnh,))
            found_by_mnh = cur.fetchone()

            target_id = edit_id
            if found_by_mnh:
                target_id = found_by_mnh["id"]

            cur.execute("""
                UPDATE devices
                SET
                    mnh = %s,
                    device_type = %s,
                    model = %s,
                    serial_number = %s,
                    computer_name = %s,
                    ip_address = %s,
                    warranty_start = %s,
                    warranty_end = %s
                WHERE id = %s
            """, (
                mnh,
                device_type,
                model,
                serial_number,
                computer_name,
                ip_address,
                warranty_start,
                warranty_end,
                target_id
            ))

            if cur.rowcount == 0 and original_mnh:
                cur.execute("""
                    UPDATE devices
                    SET
                        mnh = %s,
                        device_type = %s,
                        model = %s,
                        serial_number = %s,
                        computer_name = %s,
                        ip_address = %s,
                        warranty_start = %s,
                        warranty_end = %s
                    WHERE mnh = %s
                """, (
                    mnh,
                    device_type,
                    model,
                    serial_number,
                    computer_name,
                    ip_address,
                    warranty_start,
                    warranty_end,
                    original_mnh
                ))
        else:
            # โหมดเพิ่มข้อมูล / อัปเดตปกติด้วย MNH
            cur.execute("""
                INSERT INTO devices (
                    mnh,
                    device_type,
                    model,
                    serial_number,
                    computer_name,
                    ip_address,
                    warranty_start,
                    warranty_end
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (mnh)
                DO UPDATE SET
                    device_type = EXCLUDED.device_type,
                    model = EXCLUDED.model,
                    serial_number = EXCLUDED.serial_number,
                    computer_name = EXCLUDED.computer_name,
                    ip_address = EXCLUDED.ip_address,
                    warranty_start = EXCLUDED.warranty_start,
                    warranty_end = EXCLUDED.warranty_end
            """, (
                mnh,
                device_type,
                model,
                serial_number,
                computer_name,
                ip_address,
                warranty_start,
                warranty_end
            ))

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    return redirect_home_with_search(current_search)


@app.route("/delete/<int:id>", methods=["POST"])
def delete_device(id):
    current_search = get_search_value()

    conn = get_db()
    cur = conn.cursor()

    cur.execute("DELETE FROM devices WHERE id = %s", (id,))

    conn.commit()
    cur.close()
    conn.close()

    return redirect_home_with_search(current_search)


@app.route("/export_excel")
def export_excel():
    ensure_device_extra_columns()

    search_raw = request.args.get("search", "").strip()
    search = clean_mnh(search_raw) or search_raw

    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if search:
        keyword = f"%{search}%"
        cur.execute(f"""
            SELECT
                computer_name,
                device_type,
                model,
                serial_number,
                mnh,
                ip_address,
                warranty_start,
                warranty_end
            FROM devices
            WHERE mnh ILIKE %s
               OR device_type ILIKE %s
               OR model ILIKE %s
               OR serial_number ILIKE %s
               OR computer_name ILIKE %s
               OR ip_address ILIKE %s
               OR TO_CHAR(warranty_start, 'YYYY-MM-DD') ILIKE %s
               OR TO_CHAR(warranty_end, 'YYYY-MM-DD') ILIKE %s
            {ORDER_SQL}
        """, (keyword, keyword, keyword, keyword, keyword, keyword, keyword, keyword))
    else:
        cur.execute(f"""
            SELECT
                computer_name,
                device_type,
                model,
                serial_number,
                mnh,
                ip_address,
                warranty_start,
                warranty_end
            FROM devices
            {ORDER_SQL}
        """)

    rows = cur.fetchall()

    cur.close()
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "รายการอุปกรณ์"

    headers = [
        "ชื่อเครื่อง",
        "ประเภท",
        "รุ่น",
        "Serial Number",
        "MNH",
        "IP Address",
        "วันเริ่มต้นประกัน",
        "วันหมดประกัน"
    ]

    ws.append(headers)

    # ตั้งค่า Header
    header_fill = PatternFill("solid", fgColor="D9EAF7")
    header_font = Font(bold=True)
    thin_border = Border(
        left=Side(style="thin", color="D9E2EC"),
        right=Side(style="thin", color="D9E2EC"),
        top=Side(style="thin", color="D9E2EC"),
        bottom=Side(style="thin", color="D9E2EC"),
    )

    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
        cell.number_format = "@"

    # เพิ่มข้อมูล และบังคับทุก cell ให้เป็น Text
    for row in rows:
        ws.append([
            excel_text(row.get("computer_name")),
            excel_text(row.get("device_type")),
            excel_text(row.get("model")),
            excel_text(row.get("serial_number")),
            excel_text(row.get("mnh")),
            excel_text(row.get("ip_address")),
            excel_text(row.get("warranty_start")),
            excel_text(row.get("warranty_end")),
        ])

    for row in ws.iter_rows():
        for cell in row:
            cell.number_format = "@"
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = thin_border
            if cell.value is None:
                cell.value = ""
            else:
                cell.value = excel_text(cell.value)

    # ตั้งความกว้างคอลัมน์
    column_widths = {
        "A": 24,
        "B": 18,
        "C": 30,
        "D": 24,
        "E": 20,
        "F": 18,
        "G": 20,
        "H": 20,
    }

    for col, width in column_widths.items():
        ws.column_dimensions[col].width = width

    # Freeze header และ Filter
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    filename = "devices.xlsx"
    if search:
        safe_search = re.sub(r"[^A-Za-z0-9ก-๙_-]+", "_", search)
        filename = f"devices_{safe_search}.xlsx"

    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
