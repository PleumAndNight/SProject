"""
insert_metering_202511.py
-------------------------------------------------
1. ALTER TABLE เพิ่ม column ใหม่ (ถ้ายังไม่มี)
2. INSERT merged_202511.csv → metering_history
-------------------------------------------------
ติดตั้ง dependency:
    pip install pandas mysql-connector-python

แก้ค่า DB_CONFIG ให้ตรงกับ server ของคุณก่อนรัน
"""

import pandas as pd
import mysql.connector
from mysql.connector import Error

# =========================================================
# CONFIG — แก้ตรงนี้
# =========================================================
DB_CONFIG = {
    "host":     "localhost",
    "port":     3306,
    "user":     "root",
    "password": "",
    "database": "solar_edge",
}

CSV_FILE = "merged_202512.csv"   # path ไปยังไฟล์ (ปรับถ้าอยู่คนละ folder)
BATCH_SIZE = 500                 # จำนวน rows ต่อ INSERT batch

# =========================================================
# COLUMN MAPPING  CSV → DB
# =========================================================
# key   = ชื่อ column ใน CSV
# value = ชื่อ column ใน DB
COL_MAP = {
    "DatetimeServer":      "timestamp",
    "GTP1_Total_Power_kW": "pv_power_kw",
    "Aload_SumkW":         "load_power_kw",
    "Irr_W_m2":            "irradiance_wm2",
    "Amb temp":            "ambient_temp_c",
    "PV temp":             "panel_temp_c",
    # column ใหม่ที่จะเพิ่มใน DB
    "plim_gtp_1":          "plim_gtp_1",
    "Irrt_kWh_m2":         "irrt_kwh_m2",
}

# column ใหม่ที่ต้อง ALTER TABLE เพิ่ม (ชื่อ DB, type)
NEW_COLUMNS = [
    ("plim_gtp_1",  "DECIMAL(10,2) DEFAULT NULL COMMENT 'Power limit GTP1 kW'"),
    ("irrt_kwh_m2", "DECIMAL(10,4) DEFAULT NULL COMMENT 'Irradiation total kWh/m2'"),
]

# =========================================================
# HELPERS
# =========================================================

def add_columns_if_missing(cursor):
    """ALTER TABLE เพิ่ม column ใหม่ถ้ายังไม่มี"""
    cursor.execute("SHOW COLUMNS FROM metering_history")
    existing = {row[0] for row in cursor.fetchall()}

    for col_name, col_def in NEW_COLUMNS:
        if col_name not in existing:
            sql = f"ALTER TABLE metering_history ADD COLUMN `{col_name}` {col_def}"
            print(f"  ALTER TABLE: เพิ่ม column '{col_name}'")
            cursor.execute(sql)
        else:
            print(f"  column '{col_name}' มีอยู่แล้ว — ข้าม")


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = df.columns.str.strip()

    # เลือกเฉพาะ column ที่ map ไว้
    df = df[[c for c in COL_MAP.keys() if c in df.columns]]

    # rename → ชื่อ DB
    df = df.rename(columns=COL_MAP)

    # แปลง timestamp
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])

    # แปลง NaN → None (SQL NULL)
    df = df.where(pd.notnull(df), None)

    return df


def insert_batch(cursor, df: pd.DataFrame):
    db_cols = df.columns.tolist()
    col_list = ", ".join(f"`{c}`" for c in db_cols)
    placeholders = ", ".join(["%s"] * len(db_cols))

    sql = (
        f"INSERT INTO metering_history ({col_list}) "
        f"VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE "
        + ", ".join(f"`{c}`=VALUES(`{c}`)" for c in db_cols if c != "timestamp")
    )

    rows = [tuple(row) for row in df.itertuples(index=False, name=None)]
    cursor.executemany(sql, rows)


# =========================================================
# MAIN
# =========================================================

def main():
    print("📂 โหลด CSV...")
    df = load_csv(CSV_FILE)
    total = len(df)
    print(f"   {total:,} rows | columns: {df.columns.tolist()}")

    print("\n🔌 เชื่อมต่อ DB...")
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        print("   เชื่อมต่อสำเร็จ")

        # 1) เพิ่ม column ใหม่
        print("\n🛠  ตรวจ/เพิ่ม column ใหม่...")
        add_columns_if_missing(cursor)
        conn.commit()

        # 2) INSERT แบบ batch
        print(f"\n📥 กำลัง INSERT (batch {BATCH_SIZE} rows)...")
        inserted = 0
        for start in range(0, total, BATCH_SIZE):
            batch = df.iloc[start : start + BATCH_SIZE]
            insert_batch(cursor, batch)
            conn.commit()
            inserted += len(batch)
            print(f"   {inserted:,}/{total:,} rows", end="\r")

        print(f"\n✅ INSERT สำเร็จ {inserted:,} rows → metering_history")

    except Error as e:
        print(f"\n❌ DB Error: {e}")
        raise
    finally:
        if "cursor" in dir():
            cursor.close()
        if "conn" in dir() and conn.is_connected():
            conn.close()
            print("🔌 ปิดการเชื่อมต่อแล้ว")


if __name__ == "__main__":
    main()