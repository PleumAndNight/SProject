#!/usr/bin/env python3
"""
GridMind AI — Solar & Load Model Training Engine
=================================================
Train both Solar and Load forecast models from DB data.
Block 1 (data preparation) is NOT included here — data must
already be in full_history before running this script.

Solar Features (8):
    ghi_api_wm2, kt, temp_c, cloud_cover_pct,
    hour, month, solar_lag_24h, panel_temp

Load Features (7):
    temp_c, hour, month, dayofweek, is_weekend,
    load_lag_24h, load_lag_7d

Output files (saved ONLY when model beats current deployed):
    model_solar_v1.0.txt        — Solar LightGBM model
    model_solar_v1.0_meta.json  — Solar training metadata
    model_load_v1.0.txt         — Load LightGBM model
    model_load_v1.0_meta.json   — Load training metadata

DB Table:
    model_value (train_id, timestamp, org_id, site_id, model_type,
                 mape, rmse, deploy_status)

Usage:
    python3 train_model_load_solar.py               # run once immediately
    python3 train_model_load_solar.py --daemon       # run as midnight daemon
    python3 train_model_load_solar.py --site-id site-02 --output-dir ./models
    python3 train_model_load_solar.py --skip-solar   # train load only
    python3 train_model_load_solar.py --skip-load    # train solar only

Requirements:
    pip install pandas numpy lightgbm pvlib scikit-learn mysql-connector-python requests schedule
"""

import argparse
import hashlib
import json
import logging
import os
import time
import warnings
from datetime import datetime, timezone

import lightgbm as lgb
import mysql.connector
import numpy as np
import pandas as pd
import pvlib
import requests
import schedule
from sklearn.metrics import mean_absolute_percentage_error, mean_squared_error
from sklearn.model_selection import TimeSeriesSplit
import sys

warnings.filterwarnings("ignore")

# ============================================================
# Configuration
# ============================================================

DB_CONFIG = {
    "host": "localhost",
    "database": "solar_cloud",
    "user": "root",
    "password": "",  # <-- ใส่รหัสผ่านของคุณ
}

# org_id สำหรับบันทึกลง model_value (ปรับตาม org จริง)
DEFAULT_ORG_ID    = "org-01"

DEFAULT_SITE_ID   = "site-01"
DEFAULT_LATITUDE  = 13.75398000
DEFAULT_LONGITUDE = 100.50144000
DEFAULT_TIMEZONE  = "Asia/Bangkok"
DEFAULT_KWP       = 126.0
DEFAULT_NOCT      = 45
DEFAULT_OUTPUT    = "./gridmind_models"

SOLAR_VERSION = "v1.0"
LOAD_VERSION  = "v1.0"

SOLAR_FEATURES = [
    "ghi_api_wm2", "kt", "temp_c", "cloud_cover_pct",
    "hour", "month", "solar_lag_24h", "panel_temp",
]
LOAD_FEATURES = [
    "temp_c", "hour", "month", "dayofweek", "is_weekend",
    "load_lag_24h", "load_lag_7d",
]

MIN_ROWS = 200

# ============================================================
# Logging
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("train_model_load_solar.log", mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger("gridmind.train")


# ============================================================
# Helpers
# ============================================================

def feature_hash(features: list) -> str:
    return hashlib.md5(",".join(features).encode()).hexdigest()[:8]


def calc_psi(expected: pd.Series, actual: pd.Series, bins: int = 10) -> float:
    bp = np.linspace(
        min(expected.min(), actual.min()),
        max(expected.max(), actual.max()),
        bins + 1,
    )
    ep = np.clip(np.histogram(expected, bp)[0] / len(expected), 0.001, None)
    ap = np.clip(np.histogram(actual, bp)[0] / len(actual), 0.001, None)
    return float(np.sum((ap - ep) * np.log(ap / ep)))


def save_meta(path: str, payload: dict) -> None:
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    logger.info(f"  Meta saved: {path}")


# ============================================================
# DB: model_value helpers
# ============================================================

def get_deployed_model(cfg: dict, model_type: str) -> dict | None:
    """
    ดึงแถวล่าสุดที่ deploy_status = 'deploy' สำหรับ site และ model_type ที่กำหนด
    คืนค่า dict {mape, rmse, train_id} หรือ None ถ้าไม่มี
    """
    query = """
        SELECT train_id, mape, rmse
        FROM   model_value
        WHERE  site_id = %s
          AND  model_type = %s
          AND  deploy_status = 'deploy'
        ORDER  BY timestamp DESC
        LIMIT  1
    """
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cur  = conn.cursor(dictionary=True)
        cur.execute(query, (cfg["site_id"], model_type))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            logger.info(
                f"  [{model_type.upper()}] Current deployed → "
                f"train_id={row['train_id']} | MAPE={row['mape']} | RMSE={row['rmse']}"
            )
        else:
            logger.info(f"  [{model_type.upper()}] No deployed model found — will deploy new one")
        return row
    except mysql.connector.Error as e:
        logger.error(f"DB error fetching deployed model: {e}")
        return None


def insert_model_value(cfg: dict, model_type: str, mape: float, rmse: float,
                       deploy_status: str) -> int | None:
    """
    บันทึกผลการ train ลงตาราง model_value
    คืนค่า train_id ที่ได้รับ (AUTO_INCREMENT)
    """
    query = """
        INSERT INTO model_value
            (timestamp, org_id, site_id, model_type, mape, rmse, deploy_status)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s)
    """
    params = (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        cfg["org_id"],
        cfg["site_id"],
        model_type,
        round(mape, 2),
        round(rmse, 2),
        deploy_status,
    )
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cur  = conn.cursor()
        cur.execute(query, params)
        conn.commit()
        train_id = cur.lastrowid
        cur.close()
        conn.close()
        logger.info(
            f"  [{model_type.upper()}] model_value inserted → "
            f"train_id={train_id} | deploy_status={deploy_status} | "
            f"MAPE={mape:.2f} | RMSE={rmse:.2f}"
        )
        return train_id
    except mysql.connector.Error as e:
        logger.error(f"DB error inserting model_value: {e}")
        return None


def should_deploy(new_mape: float, new_rmse: float, deployed: dict | None) -> bool:
    """
    เปรียบเทียบโมเดลใหม่กับที่ deploy อยู่
    เงื่อนไข: deploy เมื่อ MAPE ดีขึ้น (ต่ำกว่า) เทียบกับตัวที่ deploy ล่าสุด
    ถ้ายังไม่มี deployed เลย → deploy เสมอ
    """
    if deployed is None:
        logger.info("  No existing deployed model → will deploy new model")
        return True

    current_mape = float(deployed["mape"])
    current_rmse = float(deployed["rmse"])

    logger.info(
        f"  Comparison → New: MAPE={new_mape:.2f}% RMSE={new_rmse:.2f} | "
        f"Deployed: MAPE={current_mape:.2f}% RMSE={current_rmse:.2f}"
    )

    if new_mape < current_mape:
        logger.info(f"  ✅ New model is BETTER (MAPE {new_mape:.2f}% < {current_mape:.2f}%) → DEPLOY")
        return True
    else:
        logger.warning(
            f"  ❌ New model is NOT better (MAPE {new_mape:.2f}% >= {current_mape:.2f}%) → UNDEPLOY"
        )
        return False


# ============================================================
# Step 1: Fetch data from DB
# ============================================================

def fetch_db_data(cfg: dict) -> pd.DataFrame:
    """
    ดึงข้อมูลทั้งหมดจาก full_history สำหรับ site ที่กำหนด
    คอลัมน์ที่ใช้:
        timestamp, pv_power_kw, load_power_kw,
        irradiance_wm2, ambient_temp_c, panel_temp_c
    """
    logger.info("=" * 60)
    logger.info("STEP 1: Fetching data from DB")
    logger.info(f"  Site: {cfg['site_id']}")
    logger.info("=" * 60)

    query = """
        SELECT
            timestamp,
            pv_power_kw,
            load_power_kw,
            irradiance_wm2,
            ambient_temp_c,
            panel_temp_c
        FROM full_history
        WHERE site_id = %s
        ORDER BY timestamp ASC
    """
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        df = pd.read_sql(query, conn, params=(cfg["site_id"],))
        conn.close()
    except mysql.connector.Error as e:
        logger.error(f"DB connection FAILED: {e}")
        raise

    if df.empty:
        raise ValueError(f"No data found for site_id='{cfg['site_id']}'")

    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(cfg["timezone"])
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)

    logger.info(f"  Rows fetched  : {len(df):,}")
    logger.info(f"  Date range    : {df.index.min()} '>' {df.index.max()}")
    logger.info(f"  Days covered  : {(df.index.max() - df.index.min()).days} days")
    return df


# ============================================================
# Step 2: Fetch cloud cover from Open-Meteo (for solar only)
# ============================================================

def fetch_cloud_cover(cfg: dict, start: str, end: str) -> pd.Series:
    """
    ดึง cloud_cover_pct จาก Open-Meteo Archive API (hourly → interpolate 15-min)
    คืนค่า pd.Series ที่ index ตรงกับ UTC
    ถ้า API ล้มเหลว ให้คืนค่า None (จะ fallback เป็น 50%)
    """
    logger.info("  Fetching cloud cover from Open-Meteo API...")
    try:
        res = requests.get(
            "https://archive-api.open-meteo.com/v1/archive",
            params={
                "latitude": cfg["lat"],
                "longitude": cfg["lon"],
                "start_date": start,
                "end_date": end,
                "hourly": "cloud_cover",
                "timezone": "GMT",
            },
            timeout=120,
        )
        res.raise_for_status()
        data = res.json()
        hourly = data.get("hourly", {})
        if "cloud_cover" not in hourly:
            raise ValueError("API response missing cloud_cover field")
        s = pd.Series(
            hourly["cloud_cover"],
            index=pd.to_datetime(hourly["time"], utc=True),
            name="cloud_cover_pct",
        )
        # resample hourly → 15-min
        s = s.resample("15min").interpolate(method="time")
        logger.info(f"  Cloud cover: {len(s):,} rows (API OK)")
        return s
    except Exception as e:
        logger.warning(f"  API failed ({e}) — cloud_cover will be estimated from irradiance")
        return None


# ============================================================
# BLOCK 2: Train Solar Model
# ============================================================

def train_solar(cfg: dict, df_raw: pd.DataFrame) -> dict | None:
    logger.info("\n" + "=" * 60)
    logger.info("BLOCK 2: Solar Model Training")
    logger.info(f"  Features : {len(SOLAR_FEATURES)} → {SOLAR_FEATURES}")
    logger.info(f"  KWp      : {cfg['kwp']} | NOCT: {cfg['noct']}")
    logger.info("=" * 60)

    # --- Feature Engineering ---
    logger.info("\n[1/6] Feature engineering...")
    df = df_raw[["pv_power_kw", "irradiance_wm2", "ambient_temp_c", "panel_temp_c"]].copy()
    df.rename(columns={"irradiance_wm2": "ghi_api_wm2", "ambient_temp_c": "temp_c"}, inplace=True)

    # cloud cover
    start = df.index.min().strftime("%Y-%m-%d")
    end   = df.index.max().strftime("%Y-%m-%d")
    cloud_series = fetch_cloud_cover(cfg, start, end)

    if cloud_series is not None:
        df_utc = df.copy()
        df_utc.index = df_utc.index.tz_convert("UTC")
        df_utc["cloud_cover_pct"] = cloud_series.reindex(
            df_utc.index, method="nearest", tolerance=pd.Timedelta("30min")
        )
        df["cloud_cover_pct"] = df_utc["cloud_cover_pct"].values

    df["cloud_cover_pct"] = df.get("cloud_cover_pct", pd.Series(dtype=float))
    df["cloud_cover_pct"] = df["cloud_cover_pct"].fillna(
        np.clip(100 - (df["ghi_api_wm2"] / 12), 0, 100)
    )

    # clearsky index kt
    site_loc = pvlib.location.Location(cfg["lat"], cfg["lon"], tz=cfg["timezone"])
    cs = site_loc.get_clearsky(df.index)["ghi"]
    df["kt"] = (df["ghi_api_wm2"] / (cs + 1e-6)).clip(upper=1.2)

    # time features
    df["hour"]  = df.index.hour
    df["month"] = df.index.month

    # lag 24h (96 intervals of 15-min)
    df["solar_lag_24h"] = df["pv_power_kw"].shift(96)

    # panel temperature
    nf = (cfg["noct"] - 20) / 800.0
    if "panel_temp_c" in df.columns and df["panel_temp_c"].notna().mean() > 0.5:
        df["panel_temp"] = df["panel_temp_c"].fillna(df["temp_c"] + nf * df["ghi_api_wm2"])
        logger.info("  Panel temp: PV SENSOR (real) + NOCT fallback for gaps")
    else:
        df["panel_temp"] = df["temp_c"] + nf * df["ghi_api_wm2"]
        logger.info("  Panel temp: NOCT formula (no sensor data)")

    # daytime only + outlier filter
    df = df[cs > 10]
    df = df[df["pv_power_kw"] < cfg["kwp"] * 1.2]
    df = df.dropna(subset=SOLAR_FEATURES + ["pv_power_kw"])

    n_samples = len(df)
    logger.info(f"  Training samples : {n_samples:,}")
    logger.info(f"  Date range       : {df.index.min()} → {df.index.max()}")
    logger.info(f"  PV power range   : {df['pv_power_kw'].min():.1f} – {df['pv_power_kw'].max():.1f} kW")

    if n_samples < MIN_ROWS:
        logger.error(f"ABORT: Only {n_samples} rows (need {MIN_ROWS})")
        return None

    # --- PSI Drift Check ---
    logger.info("\n[2/6] PSI drift check...")
    mid = len(df) // 2
    drift = []
    for feat in SOLAR_FEATURES:
        psi = calc_psi(df[feat].iloc[:mid], df[feat].iloc[mid:])
        status = "STABLE" if psi < 0.1 else "MODERATE" if psi < 0.25 else "DRIFT!"
        logger.info(f"  {feat:20s}: PSI={psi:.4f} [{status}]")
        if status == "DRIFT!":
            drift.append(feat)

    # --- 5-Fold Time-Series CV ---
    logger.info("\n[3/6] Training 5-Fold Time-Series CV...")
    X = df[SOLAR_FEATURES]
    y = df["pv_power_kw"].clip(upper=cfg["kwp"])
    tscv = TimeSeriesSplit(n_splits=5)
    mapes, rmses = [], []

    t0 = time.time()
    for fold, (ti, vi) in enumerate(tscv.split(X)):
        m = lgb.LGBMRegressor(
            n_estimators=1000, learning_rate=0.03, num_leaves=45,
            max_depth=9, min_data_in_leaf=30,
            feature_fraction=0.85, bagging_fraction=0.85,
            bagging_freq=1, random_state=42, verbose=-1,
        )
        m.fit(
            X.iloc[ti], y.iloc[ti],
            eval_set=[(X.iloc[vi], y.iloc[vi])],
            callbacks=[lgb.early_stopping(50, verbose=False)],
        )
        yp   = np.clip(m.predict(X.iloc[vi]), 0, cfg["kwp"])
        mask = (y.iloc[vi] > cfg["kwp"] * 0.05) & (X.iloc[vi]["ghi_api_wm2"] > 10)
        if mask.sum() > 0:
            fm = mean_absolute_percentage_error(y.iloc[vi][mask], yp[mask]) * 100
            fr = np.sqrt(mean_squared_error(y.iloc[vi][mask], yp[mask]))
        else:
            fm, fr = 0.0, 0.0
        mapes.append(fm)
        rmses.append(fr)
        logger.info(f"  Fold {fold+1}: Train={len(ti):,} | Val={len(vi):,} | MAPE={fm:.2f}% | RMSE={fr:.2f} kW")

    avg_mape = float(np.mean(mapes))
    avg_rmse = float(np.mean(rmses))
    elapsed  = time.time() - t0
    mins, secs = divmod(int(elapsed), 60)

    logger.info(f"\n  *******************************************")
    logger.info(f"  *  Solar Training Summary               *")
    logger.info(f"  *  Training samples : {n_samples:>10,}       *")
    logger.info(f"  *  Average MAPE     : {avg_mape:>10.2f} %   *")
    logger.info(f"  * Average RMSE     : {avg_rmse:>10.2f} kW  *")
    logger.info(f"  *  Training time    : {mins}m {secs:02d}s              *")
    logger.info(f"  *****************************************")

    if avg_mape < 15.0:
        logger.info("   PASS: Model accuracy is acceptable")
    else:
        logger.warning("    WARNING: High MAPE — consider adding more training data")

    # --- Compare with deployed model ---
    logger.info("\n[Deploy Check] Comparing with current deployed solar model...")
    deployed = get_deployed_model(cfg, "solar")
    deploy_ok = should_deploy(avg_mape, avg_rmse, deployed)
    deploy_status = "deploy" if deploy_ok else "undeploy"

    # --- Insert result into model_value ---
    insert_model_value(cfg, "solar", avg_mape, avg_rmse, deploy_status)

    if not deploy_ok:
        logger.warning(
            "  [SOLAR] Model file NOT saved — new model did not beat current deployed model"
        )
        meta = {
            "model_name"       : "SOLAR_LGBM",
            "model_version"    : SOLAR_VERSION,
            "site_id"          : cfg["site_id"],
            "deploy_status"    : deploy_status,
            "avg_mape"         : round(avg_mape, 2),
            "avg_rmse"         : round(avg_rmse, 2),
            "trained_at"       : datetime.now(timezone.utc).isoformat(),
            "note"             : "Model not saved: did not outperform deployed model",
        }
        return meta

    # --- Final model (full data) — only when deploy ---
    logger.info("\n[4/6] Training final model on full dataset...")
    final = lgb.LGBMRegressor(
        n_estimators=1000, learning_rate=0.03, num_leaves=45,
        max_depth=9, min_data_in_leaf=30,
        feature_fraction=0.85, bagging_fraction=0.85,
        bagging_freq=1, random_state=42, verbose=-1,
    )
    final.fit(X, y)

    # --- Feature importance ---
    logger.info("\n[5/6] Feature importance:")
    imp = final.feature_importances_
    total_imp = sum(imp)
    imp_dict = {}
    for name, val in sorted(zip(SOLAR_FEATURES, imp), key=lambda x: -x[1]):
        pct = val / total_imp * 100
        imp_dict[name] = round(pct, 2)
        bar = "█" * int(pct / 2)
        logger.info(f"  {name:20s}: {pct:5.1f}%  {bar}")

    # --- Save ---
    logger.info("\n[6/6] Saving model...")
    model_filename = f"model_solar_{SOLAR_VERSION}.txt"
    meta_filename  = f"model_solar_{SOLAR_VERSION}_meta.json"
    model_path = os.path.join(cfg["output_dir"], model_filename)
    meta_path  = os.path.join(cfg["output_dir"], meta_filename)

    final.booster_.save_model(model_path)
    size_kb = os.path.getsize(model_path) / 1024
    logger.info(f"  Model saved: {model_path} ({size_kb:.1f} KB)")

    meta = {
        "model_name"       : "SOLAR_LGBM",
        "model_version"    : SOLAR_VERSION,
        "site_id"          : cfg["site_id"],
        "features"         : SOLAR_FEATURES,
        "feature_hash"     : feature_hash(SOLAR_FEATURES),
        "hyperparameters"  : {
            "n_estimators": 1000, "learning_rate": 0.03,
            "num_leaves": 45, "max_depth": 9,
        },
        "training_samples" : n_samples,
        "date_range"       : f"{df.index.min()} to {df.index.max()}",
        "avg_mape"         : round(avg_mape, 2),
        "avg_rmse"         : round(avg_rmse, 2),
        "fold_mapes"       : [round(m, 2) for m in mapes],
        "fold_rmses"       : [round(r, 2) for r in rmses],
        "feature_importance": imp_dict,
        "drift_alerts"     : drift,
        "installed_kwp"    : cfg["kwp"],
        "noct"             : cfg["noct"],
        "deploy_status"    : deploy_status,
        "trained_at"       : datetime.now(timezone.utc).isoformat(),
    }
    save_meta(meta_path, meta)
    return meta


# ============================================================
# BLOCK 3: Train Load Model
# ============================================================

def train_load(cfg: dict, df_raw: pd.DataFrame) -> dict | None:
    logger.info("\n" + "=" * 60)
    logger.info("BLOCK 3: Load Model Training")
    logger.info(f"  Features : {len(LOAD_FEATURES)} → {LOAD_FEATURES}")
    logger.info("=" * 60)

    # --- Feature Engineering ---
    logger.info("\n[1/6] Feature engineering...")
    df = df_raw[["load_power_kw", "ambient_temp_c"]].copy()
    df.rename(columns={"ambient_temp_c": "temp_c"}, inplace=True)

    # temperature fallback
    if df["temp_c"].isnull().all():
        df["temp_c"] = 30.0
        logger.info("  Temperature: no sensor data → using 30.0°C default")
    else:
        df["temp_c"] = df["temp_c"].ffill()

    # time features
    df["hour"]      = df.index.hour
    df["month"]     = df.index.month
    df["dayofweek"] = df.index.dayofweek
    df["is_weekend"] = (df["dayofweek"] >= 5).astype(int)

    # lag features (no data leakage: lag >= 24h for 24h-ahead forecast)
    df["load_lag_24h"] = df["load_power_kw"].shift(96)   # 24h = 96 × 15-min
    df["load_lag_7d"]  = df["load_power_kw"].shift(672)  # 7d = 672 × 15-min

    df = df.dropna(subset=LOAD_FEATURES + ["load_power_kw"])

    n_samples = len(df)
    logger.info(f"  Training samples : {n_samples:,}")
    logger.info(f"  Date range       : {df.index.min()} → {df.index.max()}")
    logger.info(f"  Load range       : {df['load_power_kw'].min():.1f} – {df['load_power_kw'].max():.1f} kW")
    logger.info(f"  Weekend ratio    : {df['is_weekend'].mean()*100:.1f}%")

    if n_samples < MIN_ROWS:
        logger.error(f"ABORT: Only {n_samples} rows (need {MIN_ROWS})")
        return None

    if n_samples < 672:
        logger.warning("    Less than 7 days of data — load_lag_7d may be unreliable")

    # --- PSI Drift Check ---
    logger.info("\n[2/6] PSI drift check...")
    mid = len(df) // 2
    drift = []
    for feat in LOAD_FEATURES:
        if df[feat].nunique() > 2:
            psi = calc_psi(df[feat].iloc[:mid], df[feat].iloc[mid:])
            status = "STABLE" if psi < 0.1 else "MODERATE" if psi < 0.25 else "DRIFT!"
            logger.info(f"  {feat:20s}: PSI={psi:.4f} [{status}]")
            if status == "DRIFT!":
                drift.append(feat)

    # --- 5-Fold Time-Series CV ---
    logger.info("\n[3/6] Training 5-Fold Time-Series CV...")
    X = df[LOAD_FEATURES]
    y = df["load_power_kw"]
    tscv = TimeSeriesSplit(n_splits=5)
    mapes, rmses = [], []

    t0 = time.time()
    for fold, (ti, vi) in enumerate(tscv.split(X)):
        m = lgb.LGBMRegressor(
            n_estimators=1200, learning_rate=0.02, num_leaves=63,
            max_depth=10, min_data_in_leaf=40,
            feature_fraction=0.8, bagging_fraction=0.8,
            bagging_freq=1, random_state=42, verbose=-1,
        )
        m.fit(
            X.iloc[ti], y.iloc[ti],
            eval_set=[(X.iloc[vi], y.iloc[vi])],
            callbacks=[lgb.early_stopping(50, verbose=False)],
        )
        yp   = np.clip(m.predict(X.iloc[vi]), 0, None)
        mask = y.iloc[vi] > 1.0
        if mask.sum() > 0:
            fm = mean_absolute_percentage_error(y.iloc[vi][mask], yp[mask]) * 100
            fr = np.sqrt(mean_squared_error(y.iloc[vi][mask], yp[mask]))
        else:
            fm, fr = 0.0, 0.0
        mapes.append(fm)
        rmses.append(fr)
        logger.info(f"  Fold {fold+1}: Train={len(ti):,} | Val={len(vi):,} | MAPE={fm:.2f}% | RMSE={fr:.2f} kW")

    avg_mape = float(np.mean(mapes))
    avg_rmse = float(np.mean(rmses))
    elapsed  = time.time() - t0
    mins, secs = divmod(int(elapsed), 60)

    logger.info(f"\n  ****************************************")
    logger.info(f"  *  Load Training Summary                *")
    logger.info(f"  *  Training samples : {n_samples:>10,}       *")
    logger.info(f"  *  Average MAPE     : {avg_mape:>10.2f} %   *")
    logger.info(f"  * Average RMSE     : {avg_rmse:>10.2f} kW  *")
    logger.info(f"  *  Training time    : {mins}m {secs:02d}s              *")
    logger.info(f"  ******************************************")

    if avg_mape < 15.0:
        logger.info("   PASS: Model accuracy is acceptable")
    else:
        logger.warning("    WARNING: High MAPE — consider adding shift schedule or holiday calendar")

    # --- Compare with deployed model ---
    logger.info("\n[Deploy Check] Comparing with current deployed load model...")
    deployed = get_deployed_model(cfg, "load")
    deploy_ok = should_deploy(avg_mape, avg_rmse, deployed)
    deploy_status = "deploy" if deploy_ok else "undeploy"

    # --- Insert result into model_value ---
    insert_model_value(cfg, "load", avg_mape, avg_rmse, deploy_status)

    if not deploy_ok:
        logger.warning(
            "  [LOAD] Model file NOT saved — new model did not beat current deployed model"
        )
        meta = {
            "model_name"    : "LOAD_LGBM",
            "model_version" : LOAD_VERSION,
            "site_id"       : cfg["site_id"],
            "deploy_status" : deploy_status,
            "avg_mape"      : round(avg_mape, 2),
            "avg_rmse"      : round(avg_rmse, 2),
            "trained_at"    : datetime.now(timezone.utc).isoformat(),
            "note"          : "Model not saved: did not outperform deployed model",
        }
        return meta

    # --- Final model (full data) — only when deploy ---
    logger.info("\n[4/6] Training final model on full dataset...")
    final = lgb.LGBMRegressor(
        n_estimators=1200, learning_rate=0.02, num_leaves=63,
        max_depth=10, min_data_in_leaf=40,
        feature_fraction=0.8, bagging_fraction=0.8,
        bagging_freq=1, random_state=42, verbose=-1,
    )
    final.fit(X, y)

    # --- Feature importance ---
    logger.info("\n[5/6] Feature importance:")
    imp = final.feature_importances_
    total_imp = sum(imp)
    imp_dict = {}
    for name, val in sorted(zip(LOAD_FEATURES, imp), key=lambda x: -x[1]):
        pct = val / total_imp * 100
        imp_dict[name] = round(pct, 2)
        bar = "█" * int(pct / 2)
        logger.info(f"  {name:20s}: {pct:5.1f}%  {bar}")

    # --- Save ---
    logger.info("\n[6/6] Saving model...")
    model_filename = f"model_load_{LOAD_VERSION}.txt"
    meta_filename  = f"model_load_{LOAD_VERSION}_meta.json"
    model_path = os.path.join(cfg["output_dir"], model_filename)
    meta_path  = os.path.join(cfg["output_dir"], meta_filename)

    final.booster_.save_model(model_path)
    size_kb = os.path.getsize(model_path) / 1024
    logger.info(f"  Model saved: {model_path} ({size_kb:.1f} KB)")

    meta = {
        "model_name"        : "LOAD_LGBM",
        "model_version"     : LOAD_VERSION,
        "site_id"           : cfg["site_id"],
        "features"          : LOAD_FEATURES,
        "feature_hash"      : feature_hash(LOAD_FEATURES),
        "hyperparameters"   : {
            "n_estimators": 1200, "learning_rate": 0.02,
            "num_leaves": 63, "max_depth": 10,
        },
        "training_samples"  : n_samples,
        "date_range"        : f"{df.index.min()} to {df.index.max()}",
        "avg_mape"          : round(avg_mape, 2),
        "avg_rmse"          : round(avg_rmse, 2),
        "fold_mapes"        : [round(m, 2) for m in mapes],
        "fold_rmses"        : [round(r, 2) for r in rmses],
        "feature_importance": imp_dict,
        "drift_alerts"      : drift,
        "deploy_status"     : deploy_status,
        "trained_at"        : datetime.now(timezone.utc).isoformat(),
    }
    save_meta(meta_path, meta)
    return meta


# ============================================================
# Final Summary
# ============================================================

def print_final_summary(cfg: dict, solar_meta: dict | None, load_meta: dict | None) -> None:
    logger.info("\n" + "=" * 60)
    logger.info("PIPELINE COMPLETE — Final Summary")
    logger.info("=" * 60)

    if solar_meta:
        status = solar_meta.get("deploy_status", "unknown")
        logger.info(f"\n Solar Model: [{status.upper()}]")
        logger.info(f"   Avg MAPE : {solar_meta['avg_mape']:.2f} %")
        logger.info(f"   Avg RMSE : {solar_meta['avg_rmse']:.2f} kW")
        if status == "deploy":
            logger.info(f"   File     : model_solar_{SOLAR_VERSION}.txt")
            logger.info(f"   Samples  : {solar_meta.get('training_samples', 'N/A'):,}")
            logger.info(f"   Per-fold MAPE : {solar_meta.get('fold_mapes', [])}")
        else:
            logger.info(f"   File     : NOT SAVED (model did not beat deployed)")
    else:
        logger.info("\n  Solar Model: SKIPPED or FAILED")

    if load_meta:
        status = load_meta.get("deploy_status", "unknown")
        logger.info(f"\n Load Model: [{status.upper()}]")
        logger.info(f"   Avg MAPE : {load_meta['avg_mape']:.2f} %")
        logger.info(f"   Avg RMSE : {load_meta['avg_rmse']:.2f} kW")
        if status == "deploy":
            logger.info(f"   File     : model_load_{LOAD_VERSION}.txt")
            logger.info(f"   Samples  : {load_meta.get('training_samples', 'N/A'):,}")
            logger.info(f"   Per-fold MAPE : {load_meta.get('fold_mapes', [])}")
        else:
            logger.info(f"   File     : NOT SAVED (model did not beat deployed)")
    else:
        logger.info("\n  Load Model: SKIPPED or FAILED")

    logger.info(f"\n Output dir : {cfg['output_dir']}/")
    logger.info(f" Log file   : train_model_load_solar.log")
    logger.info("\n Next step  : deploy model files to Edge server")


# ============================================================
# Main pipeline (single run)
# ============================================================

def run_pipeline(args) -> None:
    cfg = {
        "site_id"   : args.site_id,
        "org_id"    : args.org_id,
        "lat"       : args.lat,
        "lon"       : args.lon,
        "kwp"       : args.kwp,
        "noct"      : args.noct,
        "timezone"  : args.timezone,
        "output_dir": args.output_dir,
    }

    os.makedirs(cfg["output_dir"], exist_ok=True)

    logger.info("=" * 60)
    logger.info(f"GridMind AI — Solar & Load Training Engine  [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
    logger.info("=" * 60)
    logger.info(f"  Site     : {cfg['site_id']}")
    logger.info(f"  Org      : {cfg['org_id']}")
    logger.info(f"  Location : {cfg['lat']}, {cfg['lon']}")
    logger.info(f"  System   : {cfg['kwp']} kWp | NOCT: {cfg['noct']}")
    logger.info(f"  Timezone : {cfg['timezone']}")
    logger.info(f"  Output   : {cfg['output_dir']}/")

    solar_meta = None
    load_meta  = None

    try:
        df_raw = fetch_db_data(cfg)

        if not args.skip_solar:
            solar_meta = train_solar(cfg, df_raw)
        else:
            logger.info("\n[SOLAR] Skipped (--skip-solar)")

        if not args.skip_load:
            load_meta = train_load(cfg, df_raw)
        else:
            logger.info("\n[LOAD] Skipped (--skip-load)")

    except Exception as e:
        logger.error(f"\n Fatal error: {e}")
        raise

    print_final_summary(cfg, solar_meta, load_meta)


# ============================================================
# Argument parser
# ============================================================

def parse_args():
    p = argparse.ArgumentParser(
        description="GridMind AI — Solar & Load Model Training Engine"
    )
    p.add_argument("--site-id",    type=str,   default=DEFAULT_SITE_ID)
    p.add_argument("--org-id",     type=str,   default=DEFAULT_ORG_ID,
                   help="org_id (UUID) for model_value table")
    p.add_argument("--lat",        type=float, default=DEFAULT_LATITUDE)
    p.add_argument("--lon",        type=float, default=DEFAULT_LONGITUDE)
    p.add_argument("--kwp",        type=float, default=DEFAULT_KWP,    help="Installed kWp")
    p.add_argument("--noct",       type=int,   default=DEFAULT_NOCT,   help="NOCT value")
    p.add_argument("--timezone",   type=str,   default=DEFAULT_TIMEZONE)
    p.add_argument("--output-dir", type=str,   default=DEFAULT_OUTPUT)
    p.add_argument("--skip-solar", action="store_true", help="Skip solar model training")
    p.add_argument("--skip-load",  action="store_true", help="Skip load model training")
    p.add_argument("--daemon",     action="store_true",
                   help="Run as daemon: execute once at midnight every day")
    return p.parse_args()


# ============================================================
# Entry point
# ============================================================

def main():
    args = parse_args()

    if args.daemon:
        logger.info("=" * 60)
        logger.info("Daemon mode: scheduled to run every day at 00:00")
        logger.info("=" * 60)

        # กำหนดให้รันทุกวันเที่ยงคืน (Asia/Bangkok = server local time)
        schedule.every().day.at("00:00").do(run_pipeline, args=args)

        # รันครั้งแรกเดี๋ยวนี้ด้วย เพื่อทดสอบ (ลบออกได้ถ้าไม่ต้องการ)
        logger.info("Running initial pipeline now (startup check)...")
        run_pipeline(args)

        logger.info("Waiting for next scheduled run at 00:00...")
        while True:
            schedule.run_pending()
            time.sleep(30)
    else:
        run_pipeline(args)


if __name__ == "__main__":
    main()