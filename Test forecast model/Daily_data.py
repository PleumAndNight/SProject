"""
metering_data_generator.py
---------------------------
สุ่มสร้างข้อมูล metering_history และบันทึกลง MariaDB/MySQL ทุก ๆ 1 นาที

ติดตั้ง dependencies:
    pip install mysql-connector-python

ตั้งค่าการเชื่อมต่อฐานข้อมูลได้ที่ส่วน DB_CONFIG ด้านล่าง
"""

import time
import random
import logging
import signal
import sys
from datetime import datetime

import mysql.connector
from mysql.connector import Error

# ─────────────────────────────────────────────
# ตั้งค่าการเชื่อมต่อฐานข้อมูล
# ─────────────────────────────────────────────
DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "root",
    "password": "",          # ← ใส่รหัสผ่านของคุณ
    "database": "solar_edge",
    "charset": "utf8mb4",
}

# ─────────────────────────────────────────────
# ช่วงค่าของแต่ละตัวแปร (ปรับได้ตามจริง)
# ─────────────────────────────────────────────
RANGES = {
    "pv_power_kw":        (0.0,   500.0),   # กำลังผลิตจากโซลาร์ (kW)
    "load_power_kw":      (50.0,  400.0),   # โหลดที่ใช้ (kW)
    "batt_power_kw":      (-100.0, 100.0),  # แบตฯ (+ = ชาร์จ, - = จ่ายไฟ)
    "grid_import_kw":     (0.0,   200.0),   # นำเข้าจากกริด
    "grid_export_kw":     (0.0,   150.0),   # ส่งออกไปกริด
    "genset_power_kw":    (0.0,     0.0),   # เครื่องกำเนิดไฟ (ปิดอยู่)
    "batt_soc":           (10.0,  100.0),   # State of Charge (%)
    "irradiance_wm2":     (0,     1200),    # ความเข้มแสง (W/m²)
    "ambient_temp_c":     (25.0,   40.0),   # อุณหภูมิสิ่งแวดล้อม
    "panel_temp_c":       (30.0,   75.0),   # อุณหภูมิแผงโซลาร์
    "grid_voltage_avg_v": (210,    240),     # แรงดันกริดเฉลี่ย (V)
    "grid_frequency_hz":  (49.5,   50.5),   # ความถี่กริด (Hz)
    "fuel_level_pct":     (0.0,  100.0),    # ระดับน้ำมัน (%)
    "plim_gtp_1":         (0.0,  500.0),    # Power limit GTP1 (kW)
    "irrt_kwh_m2":        (0.0,    8.0),    # Irradiation total (kWh/m²)
}

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("metering_generator.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# SQL
# ─────────────────────────────────────────────
INSERT_SQL = """
INSERT INTO metering_history (
    timestamp,
    pv_power_kw, load_power_kw, batt_power_kw,
    grid_import_kw, grid_export_kw, genset_power_kw,
    batt_soc, irradiance_wm2, ambient_temp_c, panel_temp_c,
    grid_voltage_avg_v, grid_frequency_hz, fuel_level_pct,
    plim_gtp_1, irrt_kwh_m2
) VALUES (
    %(timestamp)s,
    %(pv_power_kw)s, %(load_power_kw)s, %(batt_power_kw)s,
    %(grid_import_kw)s, %(grid_export_kw)s, %(genset_power_kw)s,
    %(batt_soc)s, %(irradiance_wm2)s, %(ambient_temp_c)s, %(panel_temp_c)s,
    %(grid_voltage_avg_v)s, %(grid_frequency_hz)s, %(fuel_level_pct)s,
    %(plim_gtp_1)s, %(irrt_kwh_m2)s
)
ON DUPLICATE KEY UPDATE
    pv_power_kw        = VALUES(pv_power_kw),
    load_power_kw      = VALUES(load_power_kw),
    batt_power_kw      = VALUES(batt_power_kw),
    grid_import_kw     = VALUES(grid_import_kw),
    grid_export_kw     = VALUES(grid_export_kw),
    genset_power_kw    = VALUES(genset_power_kw),
    batt_soc           = VALUES(batt_soc),
    irradiance_wm2     = VALUES(irradiance_wm2),
    ambient_temp_c     = VALUES(ambient_temp_c),
    panel_temp_c       = VALUES(panel_temp_c),
    grid_voltage_avg_v = VALUES(grid_voltage_avg_v),
    grid_frequency_hz  = VALUES(grid_frequency_hz),
    fuel_level_pct     = VALUES(fuel_level_pct),
    plim_gtp_1         = VALUES(plim_gtp_1),
    irrt_kwh_m2        = VALUES(irrt_kwh_m2);
"""

# ─────────────────────────────────────────────
# ฟังก์ชันสุ่มข้อมูล
# ─────────────────────────────────────────────
def generate_row() -> dict:
    """สุ่มค่าภายในช่วงที่กำหนดสำหรับ 1 แถว"""
    r = RANGES
    hour = datetime.now().hour

    # ปรับ PV และ irradiance ตามเวลากลางวัน (06:00–18:00)
    if 6 <= hour < 18:
        daylight_factor = 1.0 - abs(hour - 12) / 6.0   # peak ตอนเที่ยง
    else:
        daylight_factor = 0.0

    pv_max = r["pv_power_kw"][1] * daylight_factor
    irr_max = r["irradiance_wm2"][1] * daylight_factor

    pv_power   = round(random.uniform(0.0, pv_max), 2)
    irradiance = int(random.uniform(0, max(irr_max, 1)))
    panel_temp = round(random.uniform(30.0, 30.0 + daylight_factor * 45.0), 2)

    load_power     = round(random.uniform(*r["load_power_kw"]), 2)
    batt_power     = round(random.uniform(*r["batt_power_kw"]), 2)
    grid_import    = round(random.uniform(*r["grid_import_kw"]), 2)
    grid_export    = round(random.uniform(*r["grid_export_kw"]), 2)
    genset_power   = round(random.uniform(*r["genset_power_kw"]), 2)
    batt_soc       = round(random.uniform(*r["batt_soc"]), 2)
    ambient_temp   = round(random.uniform(*r["ambient_temp_c"]), 2)
    grid_voltage   = random.randint(*r["grid_voltage_avg_v"])
    grid_freq      = round(random.uniform(*r["grid_frequency_hz"]), 2)
    fuel_level     = round(random.uniform(*r["fuel_level_pct"]), 2)
    plim_gtp1      = round(random.uniform(*r["plim_gtp_1"]), 2)
    irrt_kwh_m2    = round(random.uniform(*r["irrt_kwh_m2"]), 4)

    return {
        "timestamp":          datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pv_power_kw":        pv_power,
        "load_power_kw":      load_power,
        "batt_power_kw":      batt_power,
        "grid_import_kw":     grid_import,
        "grid_export_kw":     grid_export,
        "genset_power_kw":    genset_power,
        "batt_soc":           batt_soc,
        "irradiance_wm2":     irradiance,
        "ambient_temp_c":     ambient_temp,
        "panel_temp_c":       panel_temp,
        "grid_voltage_avg_v": grid_voltage,
        "grid_frequency_hz":  grid_freq,
        "fuel_level_pct":     fuel_level,
        "plim_gtp_1":         plim_gtp1,
        "irrt_kwh_m2":        irrt_kwh_m2,
    }

# ─────────────────────────────────────────────
# ฟังก์ชันเชื่อมต่อ / บันทึก
# ─────────────────────────────────────────────
def get_connection():
    """สร้าง connection ใหม่"""
    return mysql.connector.connect(**DB_CONFIG)


def insert_row(conn, row: dict) -> None:
    """บันทึก 1 แถวลงฐานข้อมูล"""
    cursor = conn.cursor()
    cursor.execute(INSERT_SQL, row)
    conn.commit()
    cursor.close()

# ─────────────────────────────────────────────
# จัดการ Ctrl+C
# ─────────────────────────────────────────────
running = True

def handle_signal(sig, frame):
    global running
    logger.info("ได้รับสัญญาณหยุด กำลังปิดโปรแกรม...")
    running = False

signal.signal(signal.SIGINT,  handle_signal)
signal.signal(signal.SIGTERM, handle_signal)

# ─────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────
INTERVAL_SECONDS = 60   # ← เปลี่ยนได้ถ้าต้องการความถี่อื่น

def main():
    logger.info("=== เริ่มต้น metering_data_generator ===")
    logger.info(f"บันทึกข้อมูลทุก {INTERVAL_SECONDS} วินาที  |  กด Ctrl+C เพื่อหยุด")

    conn = None
    consecutive_errors = 0
    MAX_ERRORS = 5

    while running:
        try:
            # เชื่อมต่อ (หรือ reconnect)
            if conn is None or not conn.is_connected():
                logger.info("กำลังเชื่อมต่อฐานข้อมูล...")
                conn = get_connection()
                logger.info("เชื่อมต่อสำเร็จ")

            row = generate_row()
            insert_row(conn, row)

            logger.info(
                f"✔ บันทึกแล้ว | {row['timestamp']} | "
                f"PV={row['pv_power_kw']} kW | "
                f"Load={row['load_power_kw']} kW | "
                f"SOC={row['batt_soc']}% | "
                f"Irr={row['irradiance_wm2']} W/m²"
            )

            consecutive_errors = 0

        except Error as db_err:
            logger.error(f"Database error: {db_err}")
            consecutive_errors += 1
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None
            if consecutive_errors >= MAX_ERRORS:
                logger.critical(f"เกิดข้อผิดพลาดติดต่อกัน {MAX_ERRORS} ครั้ง หยุดโปรแกรม")
                break

        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            consecutive_errors += 1
            if consecutive_errors >= MAX_ERRORS:
                logger.critical(f"เกิดข้อผิดพลาดติดต่อกัน {MAX_ERRORS} ครั้ง หยุดโปรแกรม")
                break

        # รอจนครบ 1 นาที โดยยังคง responsive ต่อ Ctrl+C
        for _ in range(INTERVAL_SECONDS):
            if not running:
                break
            time.sleep(1)

    if conn and conn.is_connected():
        conn.close()
    logger.info("=== โปรแกรมหยุดทำงานแล้ว ===")


if __name__ == "__main__":
    main()
