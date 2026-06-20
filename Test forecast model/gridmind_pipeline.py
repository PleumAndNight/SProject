#!/usr/bin/env python3
"""
GridMind AI  — Unified Pipeline
=====================================
รวม Aggregator (avg_15min) + Inference เป็นไฟล์เดียว

Flow ทุก 15 นาที:
  STEP 1  ดึง raw จาก metering_history (Edge DB)
  STEP 2  Clean ข้อมูลผิดปกติ
  STEP 3  Hybrid filter (ตัด curtail)
  STEP 4  คำนวณ panel temperature
  STEP 5  Resample เป็น 15 นาที
  STEP 6  Upsert → avg_15min (Edge DB)
  STEP 7  Insert → full_history (Cloud DB)
  STEP 8  ดึง lag features จาก avg_15min ที่เพิ่ง upsert
  STEP 9  ดึง weather จาก Open-Meteo
  STEP 10 Predict solar + load (LightGBM / heuristic fallback)
  STEP 11 Insert → energy_forecasts (Edge DB)

Usage:
    python3 gridmind_pipeline.py
    python3 gridmind_pipeline.py --run-now          # รันทันทีรอบแรก
    python3 gridmind_pipeline.py --site-id site-02 --org-id org-01
    python3 gridmind_pipeline.py --interval 15 --overlap 2
    python3 gridmind_pipeline.py --dry-run          # ไม่ insert energy_forecasts
"""

# ─── stdlib ───────────────────────────────────────────────────────────────────
import argparse
import logging
import math
import os
import signal
import sys
import time
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

warnings.filterwarnings("ignore")

# ─── third-party ──────────────────────────────────────────────────────────────
import numpy as np
import pandas as pd
import mysql.connector
import pymysql
import pymysql.cursors
import requests

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════

# --- Paths ---
BASE_DIR         = Path(__file__).resolve().parent
MODEL_DIR        = BASE_DIR / "gridmind_models"
LOG_DIR          = BASE_DIR / "logs"
SOLAR_MODEL_PATH = MODEL_DIR / "model_solar_v1.0.txt"
LOAD_MODEL_PATH  = MODEL_DIR / "model_load_v1.0.txt"

# --- Site ---
SITE_ID              = "site-01"
ORG_ID               = "org-01"
LATITUDE             = 13.65
LONGITUDE            = 100.64
PV_CAPACITY_KW       = 126.0
INVERTER_MAX_KW      = 100.0
NOCT                 = 45
GRID_IMPORT_LIMIT_KW = 80.0

# --- Edge DB (mysql.connector) — aggregator ใช้ ---
EDGE_DB_CONFIG = {
    "host":     "127.0.0.1",
    "database": "solar_edge",
    "user":     "root",
    "password": "",
}

# --- Cloud DB (mysql.connector) — full_history ---
CLOUD_DB_CONFIG = {
    "host":     "127.0.0.1",
    "database": "solar_cloud",
    "user":     "root",
    "password": "",
}

# --- Inference DB (pymysql) — lag query + energy_forecasts ---
MYSQL_CONFIG = {
    "host":        "127.0.0.1",
    "port":        3306,
    "user":        "root",
    "password":    "",
    "database":    "solar_edge",
    "charset":     "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
}

# --- Open-Meteo ---
OPENMETEO_URL    = "https://api.open-meteo.com/v1/forecast"
OPENMETEO_PARAMS = {
    "latitude":  LATITUDE,
    "longitude": LONGITUDE,
    "current": ["temperature_2m", "relative_humidity_2m",
                "cloud_cover", "wind_speed_10m", "shortwave_radiation"],
    "hourly":  ["temperature_2m", "relative_humidity_2m",
                "cloud_cover", "shortwave_radiation", "wind_speed_10m"],
    "forecast_days": 1,
    "timezone": "Asia/Bangkok",
}

# ── Aggregator constants ──────────────────────────────────────────────────────
MIN_ROWS    = 5       # ขั้นต่ำหลัง clean (สำหรับ window 15 นาที)
BATCH_SIZE  = 500

# ══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════════════════════

def setup_logging(output_dir: str):
    log_dir = Path(output_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"gridmind_{datetime.now():%Y%m%d}.log"

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.FileHandler(str(log_file), mode="a", encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)

    ch = logging.StreamHandler(
        open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False)
    )
    ch.setFormatter(fmt)
    ch.setLevel(logging.INFO)

    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(logging.DEBUG)
        root.addHandler(fh)
        root.addHandler(ch)

logger = logging.getLogger("gridmind.pipeline")

# ══════════════════════════════════════════════════════════════════════════════
# SCHEDULER HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def get_last_window(interval_min: int, overlap_min: int):
    """คืน (ts_start, ts_end, window_end_utc) ของ window 15 นาทีที่เพิ่งสิ้นสุด
    คำนวณทั้งหมดใน UTC แล้วแปลงเป็น Bangkok naive string ตอนสุดท้าย"""
    BKK = timezone(timedelta(hours=7))
    now_bkk      = datetime.now(BKK).replace(second=0, microsecond=0)
    minutes_past = now_bkk.minute % interval_min
    window_end   = now_bkk - timedelta(minutes=minutes_past)
    window_start = window_end - timedelta(minutes=interval_min + overlap_min)

    ts_start = window_start.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    ts_end   = window_end.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    return ts_start, ts_end, window_end


def sleep_until_next_slot(interval_min: int, running: list):
    """Sleep จนถึงขอบ interval ถัดไป + 65 วินาที
    (รอให้ข้อมูลนาทีสุดท้าย flush เข้า DB ก่อนดึง)
    sleep ทีละ 1 วินาที เพื่อให้ Ctrl+C หยุดได้ทันที"""
    now          = datetime.now(timezone.utc).astimezone()
    minutes_past = now.minute % interval_min
    seconds_past = minutes_past * 60 + now.second
    wait_seconds = int((interval_min * 60 - seconds_past) + 65)
    next_run     = now + timedelta(seconds=wait_seconds)
    logger.info("⏱  Next run at %s (sleeping %ds)",
                next_run.strftime("%H:%M:%S %Z"), wait_seconds)
    for _ in range(wait_seconds):
        if not running[0]:
            break
        time.sleep(1)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION A — AGGREGATOR (STEP 1–7)
# ══════════════════════════════════════════════════════════════════════════════

# ─── STEP 1: Fetch window ────────────────────────────────────────────────────

def fetch_window(ts_start: str, ts_end: str):
    """ดึงข้อมูล raw จาก metering_history ช่วง [ts_start, ts_end)"""
    logger.info("STEP 1 | Fetch metering_history: %s → %s (Bangkok)", ts_start, ts_end)
    query = """
        SELECT timestamp, pv_power_kw, load_power_kw,
               batt_power_kw, grid_import_kw, grid_export_kw, batt_soc,
               irradiance_wm2, ambient_temp_c, panel_temp_c,
               grid_voltage_avg_v, grid_frequency_hz, plim_gtp_1
        FROM metering_history
        WHERE timestamp >= %s AND timestamp < %s
        ORDER BY timestamp ASC
    """
    try:
        conn   = mysql.connector.connect(**EDGE_DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(query, (ts_start, ts_end))
        rows   = cursor.fetchall()
        cursor.close()
        conn.close()
    except mysql.connector.Error as e:
        logger.error("STEP 1 | Edge DB FAILED: %s", e)
        return None

    if not rows:
        logger.warning("STEP 1 | No data in window — skipping")
        return None

    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp")
    df.index = df.index.tz_localize("Asia/Bangkok").tz_convert("UTC")

    for col in ["pv_power_kw", "load_power_kw",
                "batt_power_kw", "grid_import_kw", "grid_export_kw", "batt_soc",
                "irradiance_wm2", "ambient_temp_c", "panel_temp_c",
                "grid_voltage_avg_v", "grid_frequency_hz", "plim_gtp_1"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    logger.info("STEP 1 | Fetched: %d rows", len(df))
    return df


# ─── STEP 2: Clean ───────────────────────────────────────────────────────────

def clean_data(df):
    df = df.replace(-0.999, np.nan)

    for col in ["ambient_temp_c", "panel_temp_c"]:
        if col in df.columns:
            df.loc[df[col] == 0, col] = np.nan

    for col, (lo, hi) in [("ambient_temp_c", (15.0, 55.0)),
                           ("panel_temp_c",   (15.0, 85.0))]:
        if col in df.columns:
            bad = ((df[col] < lo) | (df[col] > hi)) & df[col].notna()
            df.loc[bad, col] = np.nan

    before = len(df)
    df = df.dropna(subset=["pv_power_kw", "load_power_kw"])
    logger.info("STEP 2 | Clean: %d → %d rows (dropped %d)", before, len(df), before - len(df))

    for col in ["ambient_temp_c", "irradiance_wm2", "panel_temp_c"]:
        if col in df.columns and df[col].isna().any():
            df[col] = df[col].interpolate(method="time", limit=4)

    if len(df) < MIN_ROWS:
        logger.warning("STEP 2 | Only %d rows after clean (need %d) — skipping", len(df), MIN_ROWS)
        return None
    return df


# ─── STEP 3: Hybrid Filter ───────────────────────────────────────────────────

def hybrid_filter(df):
    if "plim_gtp_1" not in df.columns or df["plim_gtp_1"].isna().all():
        logger.info("STEP 3 | plim_gtp_1 unavailable — skip filter")
        return df
    df_filt = df[df["pv_power_kw"] < (df["plim_gtp_1"] * 0.95)].copy()
    logger.info("STEP 3 | Hybrid filter: %d/%d passed", len(df_filt), len(df))
    return df_filt


# ─── STEP 4: Panel Temperature ───────────────────────────────────────────────

def calc_panel_temp(df, noct: int, kwp: float):
    nf = (noct - 20) / 800.0
    has_sensor = ("panel_temp_c" in df.columns and
                  df["panel_temp_c"].notna().sum() > len(df) * 0.3)
    if has_sensor:
        noct_calc          = df["ambient_temp_c"] + nf * df["irradiance_wm2"].fillna(0)
        df["panel_temp_calc"] = df["panel_temp_c"].fillna(noct_calc)
        logger.info("STEP 4 | Panel temp: sensor + NOCT fallback")
    else:
        df["panel_temp_calc"] = df["ambient_temp_c"] + nf * df["irradiance_wm2"].fillna(0)
        logger.info("STEP 4 | Panel temp: NOCT formula only")
    return df


# ─── STEP 5: Resample 15 min ─────────────────────────────────────────────────

def resample_15min(df):
    df_15m = pd.DataFrame({
        # ── mean ───────────────────────────────────────────────────────
        "pv_power_kw":      df["pv_power_kw"].resample("15min").mean(),
        "load_power_kw":    df["load_power_kw"].resample("15min").mean(),
        "batt_power_kw":    df["batt_power_kw"].resample("15min").mean(),
        "grid_import_kw":   df["grid_import_kw"].resample("15min").mean(),
        "grid_export_kw":   df["grid_export_kw"].resample("15min").mean(),
        "batt_soc":         df["batt_soc"].resample("15min").mean(),
        "irradiance_wm2":   df["irradiance_wm2"].resample("15min").mean(),
        "ambient_temp_c":   df["ambient_temp_c"].resample("15min").mean(),
        "panel_temp_c":     df["panel_temp_calc"].resample("15min").mean(),
        "grid_voltage_v":   df["grid_voltage_avg_v"].resample("15min").mean(),
        "grid_frequency_hz":df["grid_frequency_hz"].resample("15min").mean(),
        # ── max / min ──────────────────────────────────────────────────
        "max_pv_power_kw":  df["pv_power_kw"].resample("15min").max(),
        "max_load_power_kw":df["load_power_kw"].resample("15min").max(),
        "min_batt_soc":     df["batt_soc"].resample("15min").min(),
        # ── energy (kWh) = mean_kw × 15min ────────────────────────────
        "energy_import_kwh":df["grid_import_kw"].resample("15min").mean() * (15 / 60),
        "energy_export_kwh":df["grid_export_kw"].resample("15min").mean() * (15 / 60),
        # ── count ──────────────────────────────────────────────────────
        "sample_count":     df["pv_power_kw"].resample("15min").count(),
    }).dropna(subset=["pv_power_kw", "load_power_kw"])
    logger.info("STEP 5 | Resampled: %d intervals", len(df_15m))
    return df_15m


# ─── STEP 6: Insert → avg_15min (Edge DB) ────────────────────────────────────

def save_to_edge_avg(df_15m):
    df = df_15m.copy().reset_index()
    df["timestamp"] = (df["timestamp"]
                       .dt.tz_convert("Asia/Bangkok")
                       .dt.tz_localize(None))
    insert_sql = """
        INSERT IGNORE INTO avg_15min
            (timestamp, pv_power_kw, load_power_kw,
             irradiance_wm2, ambient_temp_c, panel_temp_c, sample_count)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    try:
        conn   = mysql.connector.connect(**EDGE_DB_CONFIG)
        cursor = conn.cursor()
    except mysql.connector.Error as e:
        logger.error("STEP 6 | Edge DB FAILED: %s", e)
        return False

    buf = []
    for _, row in df.iterrows():
        buf.append((
            row["timestamp"],
            round(float(row["pv_power_kw"]),    2) if pd.notna(row["pv_power_kw"])    else None,
            round(float(row["load_power_kw"]),  2) if pd.notna(row["load_power_kw"])  else None,
            int(row["irradiance_wm2"])             if pd.notna(row["irradiance_wm2"]) else None,
            round(float(row["ambient_temp_c"]), 2) if pd.notna(row["ambient_temp_c"]) else None,
            round(float(row["panel_temp_c"]),   2) if pd.notna(row["panel_temp_c"])   else None,
            int(row["sample_count"]),
        ))
        if len(buf) >= BATCH_SIZE:
            cursor.executemany(insert_sql, buf)
            conn.commit()
            buf = []

    if buf:
        cursor.executemany(insert_sql, buf)
        conn.commit()

    n = cursor.rowcount
    logger.info("STEP 6 | avg_15min inserted: %d rows", n)

    # ── Retention: คงไว้ไม่เกิน 672 records (7 วัน) ──────────────────────
    MAX_RECORDS = 672
    cursor.execute("SELECT COUNT(*) AS cnt FROM avg_15min")
    total = cursor.fetchone()[0]
    if total > MAX_RECORDS:
        excess = total - MAX_RECORDS
        cursor.execute("""
            DELETE FROM avg_15min
            ORDER BY timestamp ASC
            LIMIT %s
        """, (excess,))
        conn.commit()
        logger.info("STEP 6 | Retention: ลบ %d record เก่าสุด (เหลือ %d)", excess, MAX_RECORDS)

    cursor.close()
    conn.close()
    return True


# ─── STEP 7: Insert → full_history (Cloud DB) ────────────────────────────────

def save_to_cloud(df_15m, site_id: str, org_id: str):
    df = df_15m.copy().reset_index()
    df["timestamp"] = (df["timestamp"]
                       .dt.tz_convert("Asia/Bangkok")
                       .dt.tz_localize(None))
    df["org_id"]  = org_id
    df["site_id"] = site_id
    if "sample_count" not in df.columns:
        df["sample_count"] = 1

    insert_sql = """
        INSERT IGNORE INTO full_history
            (org_id, site_id, timestamp,
             pv_power_kw, load_power_kw,
             batt_power_kw, grid_import_kw, grid_export_kw, batt_soc,
             max_pv_power_kw, max_load_power_kw, min_batt_soc,
             energy_import_kwh, energy_export_kwh,
             irradiance_wm2, ambient_temp_c, panel_temp_c,
             grid_voltage_v, grid_frequency_hz,
             sample_count, last_sync_from_edge)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    try:
        conn   = mysql.connector.connect(**CLOUD_DB_CONFIG)
        cursor = conn.cursor()
    except mysql.connector.Error as e:
        logger.error("STEP 7 | Cloud DB FAILED: %s", e)
        return False

    buf = []
    now_bkk = datetime.now(timezone(timedelta(hours=7))).replace(tzinfo=None)
    for _, row in df.iterrows():
        def f(col, digits=2):
            return round(float(row[col]), digits) if col in row and pd.notna(row.get(col)) else None
        def i(col):
            return int(row[col]) if col in row and pd.notna(row.get(col)) else None

        buf.append((
            str(row["org_id"]), str(row["site_id"]), row["timestamp"],
            f("pv_power_kw"),       f("load_power_kw"),
            f("batt_power_kw"),     f("grid_import_kw"),   f("grid_export_kw"),
            f("batt_soc"),
            f("max_pv_power_kw"),   f("max_load_power_kw"), f("min_batt_soc"),
            f("energy_import_kwh"), f("energy_export_kwh"),
            i("irradiance_wm2"),    f("ambient_temp_c"),    f("panel_temp_c"),
            i("grid_voltage_v"),    f("grid_frequency_hz"),
            int(row["sample_count"]),
            now_bkk,                # last_sync_from_edge
        ))
        if len(buf) >= BATCH_SIZE:
            cursor.executemany(insert_sql, buf)
            conn.commit()
            buf = []

    if buf:
        cursor.executemany(insert_sql, buf)
        conn.commit()

    inserted = cursor.rowcount
    cursor.close()
    conn.close()
    logger.info("STEP 7 | full_history inserted: %d rows", inserted)
    return True


# ─── Ensure avg_15min table ──────────────────────────────────────────────────

CREATE_AVG_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS `avg_15min` (
  `timestamp`      datetime      NOT NULL,
  `pv_power_kw`    decimal(10,2) DEFAULT NULL,
  `load_power_kw`  decimal(10,2) DEFAULT NULL,
  `irradiance_wm2` int(11)       DEFAULT NULL,
  `ambient_temp_c` decimal(5,2)  DEFAULT NULL,
  `panel_temp_c`   decimal(5,2)  DEFAULT NULL,
  `sample_count`   int(11)       DEFAULT NULL,
  PRIMARY KEY (`timestamp`),
  KEY `idx_timestamp` (`timestamp`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
PARTITION BY RANGE (to_days(`timestamp`))
(
  PARTITION p_2025_q1 VALUES LESS THAN (739251),
  PARTITION p_2025_q2 VALUES LESS THAN (739342),
  PARTITION p_2025_q3 VALUES LESS THAN (739434),
  PARTITION p_2025_q4 VALUES LESS THAN (739525),
  PARTITION p_2026_q1 VALUES LESS THAN (740072),
  PARTITION p_2026_q2 VALUES LESS THAN (740163),
  PARTITION p_2026_q3 VALUES LESS THAN (740255),
  PARTITION p_2026_q4 VALUES LESS THAN (740347),
  PARTITION p_future  VALUES LESS THAN MAXVALUE
);
"""

def ensure_avg_table():
    try:
        conn   = mysql.connector.connect(**EDGE_DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute(CREATE_AVG_TABLE_SQL)
        conn.commit()
        cursor.close()
        conn.close()
        logger.info("avg_15min table: ready")
    except mysql.connector.Error as e:
        logger.error("Cannot create avg_15min table: %s", e)
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION B — INFERENCE (STEP 8–11)
# ══════════════════════════════════════════════════════════════════════════════

# ─── STEP 8: Lag features จาก avg_15min ─────────────────────────────────────

def get_lag_from_avg15(target_dt=None):
    if target_dt is None:
        target_dt = datetime.now()
    ref_24h = target_dt - timedelta(hours=24)
    ref_7d  = target_dt - timedelta(days=7)

    result = {
        "solar_lag_24h":  0.0,
        "load_lag_24h":   0.0,
        "load_lag_7d":    0.0,
        "panel_temp_now": None,
        "irradiance_now": None,
    }
    try:
        conn = pymysql.connect(**MYSQL_CONFIG)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT pv_power_kw, load_power_kw FROM avg_15min
                WHERE timestamp <= %s ORDER BY timestamp DESC LIMIT 1
            """, (ref_24h,))
            row = cur.fetchone()
            if row:
                result["solar_lag_24h"] = float(row["pv_power_kw"]  or 0)
                result["load_lag_24h"]  = float(row["load_power_kw"] or 0)

            cur.execute("""
                SELECT load_power_kw FROM avg_15min
                WHERE timestamp <= %s ORDER BY timestamp DESC LIMIT 1
            """, (ref_7d,))
            row = cur.fetchone()
            if row and row["load_power_kw"] is not None:
                result["load_lag_7d"] = float(row["load_power_kw"])

            cur.execute("""
                SELECT panel_temp_c, irradiance_wm2 FROM avg_15min
                WHERE timestamp <= %s ORDER BY timestamp DESC LIMIT 1
            """, (target_dt,))
            row = cur.fetchone()
            if row:
                result["panel_temp_now"] = (float(row["panel_temp_c"])
                                            if row["panel_temp_c"] is not None else None)
                result["irradiance_now"] = (float(row["irradiance_wm2"])
                                            if row["irradiance_wm2"] is not None else None)
        conn.close()
        logger.debug("STEP 8 | Lag: solar_24h=%.1f load_24h=%.1f load_7d=%.1f",
                     result["solar_lag_24h"], result["load_lag_24h"], result["load_lag_7d"])
    except Exception as e:
        logger.error("STEP 8 | avg_15min lag query error: %s — ใช้ default", e)
    return result


# ─── STEP 9: Weather ─────────────────────────────────────────────────────────

_hourly_cache      = None
_hourly_cache_time = None
_CACHE_TTL_MIN     = 10


def _fetch_hourly_raw():
    global _hourly_cache, _hourly_cache_time
    now = datetime.now()
    if (_hourly_cache is not None and _hourly_cache_time is not None
            and (now - _hourly_cache_time).total_seconds() < _CACHE_TTL_MIN * 60):
        return _hourly_cache
    try:
        resp   = requests.get(OPENMETEO_URL, params=OPENMETEO_PARAMS, timeout=15)
        resp.raise_for_status()
        data   = resp.json()
        hourly = data.get("hourly", {})
        times  = hourly.get("time", [])
        rows   = [{
            "time":                 t,
            "temperature_2m":       hourly["temperature_2m"][i],
            "relative_humidity_2m": hourly["relative_humidity_2m"][i],
            "cloud_cover":          hourly["cloud_cover"][i],
            "shortwave_radiation":  hourly["shortwave_radiation"][i],
            "wind_speed_10m":       hourly["wind_speed_10m"][i],
        } for i, t in enumerate(times)]
        _hourly_cache      = rows
        _hourly_cache_time = now
        logger.info("STEP 9 | Weather API: %d hours fetched", len(rows))
        return rows
    except requests.RequestException as e:
        logger.error("STEP 9 | Weather API error: %s", e)
        return _hourly_cache


def _fetch_current_fallback():
    try:
        resp = requests.get(OPENMETEO_URL, params=OPENMETEO_PARAMS, timeout=15)
        resp.raise_for_status()
        cur  = resp.json().get("current", {})
        return {
            "temperature_2m":       cur.get("temperature_2m", 30),
            "relative_humidity_2m": cur.get("relative_humidity_2m", 60),
            "cloud_cover":          cur.get("cloud_cover", 50),
            "wind_speed_10m":       cur.get("wind_speed_10m", 2),
            "shortwave_radiation":  cur.get("shortwave_radiation", 0),
        }
    except Exception as e:
        logger.error("STEP 9 | Current fallback error: %s", e)
        return None


def get_weather_at(dt=None):
    if dt is None:
        dt = datetime.now()
    hourly = _fetch_hourly_raw()
    if not hourly:
        return None

    hour_floor = dt.replace(minute=0, second=0, microsecond=0)
    hour_ceil  = hour_floor + timedelta(hours=1)
    fraction   = dt.minute / 60.0
    floor_str  = hour_floor.strftime("%Y-%m-%dT%H:00")
    ceil_str   = hour_ceil.strftime("%Y-%m-%dT%H:00")

    data_a = next((h for h in hourly if h["time"] == floor_str), None)
    data_b = next((h for h in hourly if h["time"] == ceil_str),  None)

    if data_a is None and data_b is None:
        return _fetch_current_fallback()
    data_a = data_a or data_b
    data_b = data_b or data_a

    result = {}
    for f in ["temperature_2m", "relative_humidity_2m",
              "cloud_cover", "shortwave_radiation", "wind_speed_10m"]:
        a, b = data_a.get(f, 0) or 0, data_b.get(f, 0) or 0
        result[f] = round(a + (b - a) * fraction, 1)
    result["shortwave_radiation"] = max(0, result["shortwave_radiation"])
    return result


def derive_time_features(dt=None):
    if dt is None:
        dt = datetime.now()
    hour  = dt.hour + dt.minute / 60.0
    month = dt.month
    return {
        "hour":        hour,
        "month":       month,
        "day_of_year": dt.timetuple().tm_yday,
        "hour_sin":    math.sin(2 * math.pi * hour / 24),
        "hour_cos":    math.cos(2 * math.pi * hour / 24),
        "is_daytime":  1 if 6 <= hour <= 18 else 0,
    }


# ─── STEP 10: Predict ────────────────────────────────────────────────────────

_solar_model   = None
_load_model    = None
_models_loaded = False
_use_heuristic = False

_ETR_MONTHLY = {
    1: 950, 2: 1000, 3: 1050, 4: 1080, 5: 1070,  6: 1060,
    7: 1065, 8: 1075, 9: 1050, 10: 1010, 11: 960, 12: 935,
}


def load_models():
    global _solar_model, _load_model, _models_loaded, _use_heuristic
    try:
        import lightgbm as lgb
    except ImportError:
        logger.warning("lightgbm ไม่ได้ติดตั้ง — ใช้ heuristic fallback")
        _use_heuristic = True; _models_loaded = True; return

    missing = [p for p in [SOLAR_MODEL_PATH, LOAD_MODEL_PATH] if not Path(p).exists()]
    if missing:
        logger.warning("Model files missing: %s — heuristic fallback", [str(m) for m in missing])
        _use_heuristic = True; _models_loaded = True; return

    try:
        _solar_model = lgb.Booster(model_file=str(SOLAR_MODEL_PATH))
        _load_model  = lgb.Booster(model_file=str(LOAD_MODEL_PATH))
        logger.info("✅ LightGBM models loaded")
        _use_heuristic = False
    except Exception as e:
        logger.error("Model load error: %s — heuristic fallback", e)
        _use_heuristic = True
    _models_loaded = True


def _calc_kt(ghi, month, hour):
    if ghi <= 0 or hour < 6 or hour > 18:
        return 0.0
    etr      = _ETR_MONTHLY.get(int(month), 1000)
    elev_fac = max(0.1, math.cos(math.radians(abs(hour - 12) * 15)))
    return min(ghi / max(etr * elev_fac, 1.0), 1.2)


def _calc_panel_temp_inf(temp_c, ghi, wind=1.0, panel_temp_sensor=None):
    if panel_temp_sensor is not None:
        return float(panel_temp_sensor)
    pt = temp_c + (NOCT - 20) * (ghi / 800.0)
    if wind > 1:
        pt -= (wind - 1) * 1.0
    return max(temp_c, pt)


def _heuristic_solar(weather, time_feats):
    ghi   = weather.get("shortwave_radiation", 0)
    cloud = weather.get("cloud_cover", 50)
    if not time_feats.get("is_daytime") or ghi < 10:
        return 0.0
    return max(0.0, min((ghi / 1000.0) * PV_CAPACITY_KW * 0.85
                        * (1 - cloud / 100 * 0.6), PV_CAPACITY_KW))


def _heuristic_load(weather, time_feats):
    hour = time_feats.get("hour", 12)
    temp = weather.get("temperature_2m", 30)
    base = PV_CAPACITY_KW * 0.3
    tod  = (1.0 if 8 <= hour <= 17 else
            0.7 if 17 < hour <= 21 else
            0.6 if 6 <= hour < 8 else 0.3)
    return max(0.0, base * tod * (1 + max(0, temp - 28) * 0.03))


def predict(weather, time_feats, metering, target_dt=None):
    if not _models_loaded:
        load_models()
    if _use_heuristic:
        solar_kw = _heuristic_solar(weather, time_feats)
        load_kw  = _heuristic_load(weather, time_feats)
        method   = "heuristic"
    else:
        try:
            # --- Solar ---
            ghi        = weather.get("shortwave_radiation", 0)
            temp_c     = weather.get("temperature_2m", 30)
            cloud      = weather.get("cloud_cover", 50)
            wind       = weather.get("wind_speed_10m", 1)
            hour       = time_feats.get("hour", 12)
            month      = time_feats.get("month", 6)
            kt         = _calc_kt(ghi, month, hour)
            panel_temp = _calc_panel_temp_inf(temp_c, ghi, wind,
                                              metering.get("panel_temp_now"))
            X_solar    = np.array([[ghi, kt, temp_c, cloud, hour, month,
                                    metering["solar_lag_24h"], panel_temp]])
            solar_kw   = float(_solar_model.predict(X_solar)[0])

            # --- Load ---
            dt         = target_dt or datetime.now()
            dayofweek  = dt.weekday()
            is_weekend = 1 if dayofweek >= 5 else 0
            X_load     = np.array([[temp_c, hour, month, dayofweek, is_weekend,
                                    metering["load_lag_24h"], metering["load_lag_7d"]]])
            load_kw    = float(_load_model.predict(X_load)[0])
            method     = "lgbm_model"
        except Exception as e:
            logger.error("STEP 10 | LightGBM predict error: %s — fallback", e)
            solar_kw = _heuristic_solar(weather, time_feats)
            load_kw  = _heuristic_load(weather, time_feats)
            method   = "heuristic_fallback"

    solar_kw = max(0.0, round(solar_kw, 2))
    load_kw  = max(0.0, round(load_kw,  2))
    return {
        "solar_kw":    solar_kw,
        "load_kw":     load_kw,
        "net_grid_kw": round(load_kw - solar_kw, 2),
        "method":      method,
    }


# ─── STEP 11: บันทึก energy_forecasts ────────────────────────────────────────

def insert_forecast_energy(ts_str: str, pred: dict, dry_run: bool):
    if dry_run:
        logger.info("STEP 11 | [DRY_RUN] ไม่ insert energy_forecasts")
        return
    try:
        conn = pymysql.connect(**MYSQL_CONFIG)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO energy_forecasts
                    (target_time, forecast_horizon_minutes,
                     solar_gen_forecast, load_cons_forecast, net_energy_kw,
                     source, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
            """, (
                ts_str, 0,
                pred["solar_kw"], pred["load_kw"], pred["net_grid_kw"],
                "CLOUD" if pred["method"] == "lgbm_model" else "OFFLINE_CACHE",
            ))
        conn.commit()
        conn.close()
        logger.info("STEP 11 | energy_forecasts inserted")
    except Exception as e:
        logger.error("STEP 11 | Insert energy_forecasts FAILED: %s", e)


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE — รัน STEP 1–11 ในรอบเดียว
# ══════════════════════════════════════════════════════════════════════════════

def run_pipeline(cfg: dict):
    BKK = timezone(timedelta(hours=7))
    now_bkk = datetime.now(BKK)
    now_local = now_bkk.replace(tzinfo=None)   # naive Bangkok — ใช้ส่ง DB / inference
    ts_str  = now_bkk.strftime("%Y-%m-%d %H:%M:%S (Bangkok)")

    ts_start, ts_end, _ = get_last_window(cfg["INTERVAL"], cfg["OVERLAP"])

    logger.info("═" * 60)
    logger.info("🔄 Pipeline @ %s  |  Window: %s → %s", ts_str, ts_start, ts_end)
    logger.info("═" * 60)

    # ── AGGREGATOR ──────────────────────────────────────────────────────────
    df_raw = fetch_window(ts_start, ts_end)          # STEP 1
    if df_raw is None:
        logger.warning("Pipeline aborted — no raw data")
        return

    df_clean = clean_data(df_raw)                    # STEP 2
    if df_clean is None:
        logger.warning("Pipeline aborted — insufficient data after clean")
        return

    df_filt = hybrid_filter(df_clean)                # STEP 3
    df_temp = calc_panel_temp(df_filt,               # STEP 4
                              cfg["NOCT"], cfg["KWP"])
    df_15m  = resample_15min(df_temp)                # STEP 5

    if df_15m.empty:
        logger.warning("Pipeline aborted — no intervals after resample")
        return

    save_to_edge_avg(df_15m)                         # STEP 6
    save_to_cloud(df_15m, cfg["SITE_ID"], cfg["ORG_ID"])  # STEP 7

    # ── INFERENCE ───────────────────────────────────────────────────────────
    target_dt  = now_local + timedelta(hours=24)          # พรุ่งนี้เวลาเดียวกัน
    target_str = target_dt.strftime("%Y-%m-%d %H:%M:%S")

    metering   = get_lag_from_avg15(now_local)            # STEP 8
    weather    = get_weather_at(target_dt)                # STEP 9 — ดึง weather ของ target
    if weather is None:
        logger.error("STEP 9 | ไม่มีข้อมูลอากาศ — ข้าม inference")
        return

    time_feats = derive_time_features(target_dt)          # features ของเวลา target
    pred       = predict(weather, time_feats,             # STEP 10
                         metering, target_dt=target_dt)
    insert_forecast_energy(target_str, pred, cfg["DRY_RUN"])  # STEP 11

    # ── Summary ─────────────────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print(f"  🕐  Run   : {ts_str}  |  Method: {pred['method']}"
          + ("  [DRY_RUN]" if cfg["DRY_RUN"] else ""))
    print(f"  🎯  Target: {target_str}  (+24h forecast)")
    print(f"  ☀️   Solar  : {pred['solar_kw']:>8.2f} kW")
    print(f"  🏭  Load   : {pred['load_kw']:>8.2f} kW")
    print(f"  ⚡  Net    : {pred['net_grid_kw']:>8.2f} kW  "
          f"({'ซื้อไฟ' if pred['net_grid_kw'] > 0 else 'เหลือขาย'})")
    print(f"  📡  Lag    : solar_24h={metering['solar_lag_24h']:.1f}  "
          f"load_24h={metering['load_lag_24h']:.1f}  "
          f"load_7d={metering['load_lag_7d']:.1f} kW")
    print(f"  📦  Agg    : {len(df_15m)} interval(s) saved to avg_15min")
    print(f"{'─'*60}\n")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="GridMind AI — Unified Pipeline (Aggregator + Inference)"
    )
    parser.add_argument("--site-id",    type=str,   default=os.getenv("GRIDMIND_SITE_ID", "site-01"))
    parser.add_argument("--org-id",     type=str,   default=os.getenv("GRIDMIND_ORG_ID",  "org-01"))
    parser.add_argument("--kwp",        type=float, default=float(os.getenv("INSTALLED_KWP", str(PV_CAPACITY_KW))))
    parser.add_argument("--noct",       type=int,   default=NOCT)
    parser.add_argument("--output-dir", type=str,   default="./gridmind_output")
    parser.add_argument("--interval",   type=int,   default=15,  help="รันทุกกี่นาที (default=15)")
    parser.add_argument("--overlap",    type=int,   default=2,   help="ดึงเผื่อย้อนหลังกี่นาที (default=2)")
    parser.add_argument("--run-now",    action="store_true", help="รันทันทีรอบแรก ไม่รอขอบ")
    parser.add_argument("--dry-run",    action="store_true", help="ไม่ insert energy_forecasts")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    setup_logging(args.output_dir)

    cfg = {
        "SITE_ID":    args.site_id,
        "ORG_ID":     args.org_id,
        "KWP":        args.kwp,
        "NOCT":       args.noct,
        "OUTPUT_DIR": args.output_dir,
        "INTERVAL":   args.interval,
        "OVERLAP":    args.overlap,
        "DRY_RUN":    args.dry_run,
    }

    print("""
╔══════════════════════════════════════════════════════╗
║       GridMind AI — Unified Pipeline           ║
╠══════════════════════════════════════════════════════╣
║  Site:      {site:<10s}  Org: {org:<10s}           ║
║  PV:        {kwp:>6.0f} kWp   NOCT: {noct:>2d}°C            ║
║  Interval:  {interval:>4d} min    Overlap: +{overlap} min         ║
║  Mode:      {mode:<10s}                             ║
╚══════════════════════════════════════════════════════╝
    """.format(
        site=cfg["SITE_ID"], org=cfg["ORG_ID"],
        kwp=cfg["KWP"], noct=cfg["NOCT"],
        interval=cfg["INTERVAL"], overlap=cfg["OVERLAP"],
        mode="DRY_RUN" if cfg["DRY_RUN"] else "LIVE",
    ))

    ensure_avg_table()
    load_models()

    # Graceful shutdown
    running = [True]
    def _stop(sig, frame):
        print("\n🛑 Shutting down gracefully...")
        running[0] = False
    signal.signal(signal.SIGINT,  _stop)
    signal.signal(signal.SIGTERM, _stop)

    if args.run_now:
        logger.info("--run-now: executing pipeline immediately...")
        try:
            run_pipeline(cfg)
        except Exception as e:
            logger.error("Pipeline error (run-now): %s", e, exc_info=True)

    logger.info("Scheduler started — waiting for next %d-min slot. Ctrl+C to stop.",
                cfg["INTERVAL"])
    cycle = 0
    while running[0]:
        try:
            sleep_until_next_slot(cfg["INTERVAL"], running)
            if not running[0]:
                break
            cycle += 1
            logger.info("─── Cycle #%d ───", cycle)
            run_pipeline(cfg)
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error("Cycle #%d error: %s — retry next slot", cycle, e, exc_info=True)
            for _ in range(30):
                if not running[0]:
                    break
                time.sleep(1)

    print("✅ GridMind pipeline stopped.")


if __name__ == "__main__":
    main()