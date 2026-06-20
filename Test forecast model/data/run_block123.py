#!/usr/bin/env python3
"""
GridMind AI V2.2 — Block 1 + 2 + 3 Complete Runner
=====================================================
Single script that runs the entire data preparation + training pipeline.
Connects to REAL Open-Meteo Archive API for weather data.

Requirements:
    pip install pandas numpy lightgbm pvlib scikit-learn requests

Usage:
    # Put your .txt data files in a folder, then:
    cd /path/to/your/data/folder
    python3 run_block123.py

    # Or specify paths:
    python3 run_block123.py --data-dir /path/to/data --output-dir /path/to/output

    # Override site config:
    python3 run_block123.py --lat 13.65 --lon 100.64 --kwp 126.0
"""
import pandas as pd
import numpy as np
import lightgbm as lgb
import pvlib
import requests
import os
import sys
import json
import glob
import hashlib
import logging
import warnings
import argparse
from datetime import datetime, timezone
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import mean_absolute_percentage_error, mean_squared_error

warnings.filterwarnings('ignore')

import mysql.connector

DB_CONFIG = {
    'host': 'localhost',
    'database': 'gridmind_cloud',
    'user': 'root',
    'password': ''  # <-- ใส่รหัสผ่านของคุณ
}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('block123_run.log', mode='w'),
    ]
)
logger = logging.getLogger('gridmind')


# ============================================================
# Configuration
# ============================================================
def get_config(args):
    return {
        'SITE_ID':  args.site_id or os.getenv('GRIDMIND_SITE_ID', 'site-01'),
        'ORG_ID':   args.org_id or os.getenv('GRIDMIND_ORG_ID', 'org-01'),
        'LAT':      args.lat or float(os.getenv('SITE_LATITUDE', '13.650696')),
        'LON':      args.lon or float(os.getenv('SITE_LONGITUDE', '100.639835')),
        'KWP':      args.kwp or float(os.getenv('INSTALLED_KWP', '126.0')),
        'NOCT':     args.noct or int(os.getenv('NOCT', '45')),
        'DATA_DIR': args.data_dir or '.',
        'OUTPUT_DIR': args.output_dir or './gridmind_output',
        'PATTERN':  args.pattern or os.getenv('DATA_FILE_PATTERN', '20*.txt'),
        # Column names (match your file headers)
        'COL_TIMESTAMP':    args.col_timestamp or 'DatetimeServer',
        'COL_PV_POWER':     args.col_pv or 'GTP1_Total_Power_kW',
        'COL_PLIM':         args.col_plim or 'plim_gtp_1',
        'COL_LOAD':         args.col_load or 'Aload_SumkW',
        'COL_AMBIENT_TEMP': args.col_temp or 'amb temp',
        'COL_IRRADIANCE':   args.col_irr or 'Irradiance_Wm2',
        'COL_PV_TEMP':      'PV temp',
    }


SOLAR_FEATURES = ['ghi_api_wm2','kt','temp_c','cloud_cover_pct',
                   'hour','month','solar_lag_24h','panel_temp']
LOAD_FEATURES = ['temp_c','hour','month','dayofweek','is_weekend',
                  'load_lag_24h','load_lag_7d']
MIN_ROWS = 200


# ============================================================
# Helpers
# ============================================================
def calc_psi(e, a, bins=10):
    bp = np.linspace(min(e.min(),a.min()), max(e.max(),a.max()), bins+1)
    ep = np.clip(np.histogram(e,bp)[0]/len(e), 0.001, None)
    ap = np.clip(np.histogram(a,bp)[0]/len(a), 0.001, None)
    return float(np.sum((ap-ep)*np.log(ap/ep)))

def feature_hash(f):
    return hashlib.md5(','.join(f).encode()).hexdigest()[:8]


# ============================================================
# BLOCK 1: Data Preparation
# ============================================================
def run_block1(cfg):
    logger.info('=' * 60)
    logger.info('BLOCK 1: Data Preparation')
    logger.info(f'Site: {cfg["SITE_ID"]} | NOCT: {cfg["NOCT"]}')
    logger.info('=' * 60)

    C = cfg  # shorthand

    # Step 1: Load main data files (auto-detect .txt vs .csv format)
    txt_files = sorted([f for f in glob.glob(os.path.join(C['DATA_DIR'], '20*.txt'))
                        if 'pv_temp' not in os.path.basename(f).lower()])
    csv_files = sorted([f for f in glob.glob(os.path.join(C['DATA_DIR'], '20*.csv'))
                        if 'pv_temp' not in os.path.basename(f).lower()])
    all_files = txt_files + csv_files
    if not all_files:
        logger.error(f'No data files found in {C["DATA_DIR"]}')
        sys.exit(1)

    logger.info(f'\n[1/8] Loading {len(all_files)} data file(s):')
    dfs = []
    for f in all_files:
        try:
            fname = os.path.basename(f)
            is_csv = fname.lower().endswith('.csv')
            if is_csv:
                chunk = pd.read_csv(f, sep=',', encoding='utf-8-sig')
                fmt_label = 'CSV'
            else:
                chunk = pd.read_csv(f, sep='\t')
                fmt_label = 'TXT'

            # Normalize column names (case-insensitive)
            col_map = {}
            for col in chunk.columns:
                cl = col.strip().lower()
                if cl == 'amb temp':
                    col_map[col] = 'amb temp'
                elif cl in ('irr_w_m2', 'irradiance_wm2'):
                    col_map[col] = 'Irradiance_Wm2'
            if col_map:
                chunk.rename(columns=col_map, inplace=True)

            # Auto-detect and normalize PLIM scale (>500 = different unit)
            plim_info = ''
            if C['COL_PLIM'] in chunk.columns:
                pv = pd.to_numeric(chunk[C['COL_PLIM']], errors='coerce').replace(-0.999, np.nan).dropna()
                if len(pv) > 0 and pv.max() > 500:
                    chunk[C['COL_PLIM']] = pd.to_numeric(chunk[C['COL_PLIM']], errors='coerce') / 100.0
                    plim_info = ' PLIM/100'

            chunk['_is_csv'] = is_csv
            dfs.append(chunk)
            logger.info(f'  {fname}: {len(chunk):,} rows [{fmt_label}]{plim_info}')
        except Exception as e:
            logger.warning(f'  Failed: {f}: {e}')

    if not dfs:
        logger.error('No data loaded'); sys.exit(1)
    df = pd.concat(dfs, ignore_index=True)
    logger.info(f'Total raw: {len(df):,} rows')

    # Auto-detect separate PV temp CSV files
    pv_temp_files = sorted(glob.glob(os.path.join(C['DATA_DIR'], 'pv_temp_*.csv')))

    df_pv_temp = None
    if pv_temp_files:
        logger.info(f'\n  Found {len(pv_temp_files)} PV temp file(s):')
        pv_dfs = []
        for f in pv_temp_files:
            try:
                pv_chunk = pd.read_csv(f, encoding='utf-8-sig')
                for col in list(pv_chunk.columns):
                    if col.strip().lower() == 'irr_w_m2':
                        pv_chunk.rename(columns={col: 'Irradiance_Wm2'}, inplace=True)
                pv_dfs.append(pv_chunk)
                logger.info(f'    {os.path.basename(f)}: {len(pv_chunk):,} rows')
            except Exception as e:
                logger.warning(f'    Failed: {f}: {e}')
        if pv_dfs:
            df_pv_temp = pd.concat(pv_dfs, ignore_index=True)
            df_pv_temp['timestamp'] = pd.to_datetime(
                df_pv_temp['DatetimeServer'], format='mixed', dayfirst=True, errors='coerce')
            df_pv_temp = df_pv_temp.dropna(subset=['timestamp'])
            logger.info(f'  Total: {len(df_pv_temp):,} rows | {df_pv_temp["timestamp"].min()} to {df_pv_temp["timestamp"].max()}')

    # Step 2: Parse timestamps (M/D/Y for .txt, D/M/Y for .csv)
    logger.info('\n[2/8] Parsing timestamps...')
    is_csv = df['_is_csv'] == True
    if (~is_csv).any():
        df.loc[~is_csv, 'timestamp'] = pd.to_datetime(
            df.loc[~is_csv, C['COL_TIMESTAMP']], format='mixed', dayfirst=False, errors='coerce')
        logger.info(f'  .txt (M/D/Y): {df.loc[~is_csv, "timestamp"].notna().sum():,}')
    if is_csv.any():
        df.loc[is_csv, 'timestamp'] = pd.to_datetime(
            df.loc[is_csv, C['COL_TIMESTAMP']], format='mixed', dayfirst=True, errors='coerce')
        logger.info(f'  .csv (D/M/Y): {df.loc[is_csv, "timestamp"].notna().sum():,}')
    df = df.drop(columns=['_is_csv'])
    before = len(df)
    df = df.dropna(subset=['timestamp'])
    logger.info(f'  Total: {len(df):,} / {before:,} (lost {before-len(df)})')

    # Merge PV temp + Irradiance from separate files
    if df_pv_temp is not None and len(df_pv_temp) > 0:
        logger.info(f'  Merging from PV temp files...')
        df = df.sort_values('timestamp')
        df_pv_temp = df_pv_temp.sort_values('timestamp')

        merge_cols = ['timestamp']
        if 'PV temp' in df_pv_temp.columns:
            if 'PV temp' in df.columns: df = df.drop(columns=['PV temp'])
            merge_cols.append('PV temp')

        has_irr_pv = 'Irradiance_Wm2' in df_pv_temp.columns
        had_irr_main = 'Irradiance_Wm2' in df.columns
        if has_irr_pv:
            if had_irr_main:
                df.rename(columns={'Irradiance_Wm2': '_irr_main'}, inplace=True)
            merge_cols.append('Irradiance_Wm2')

        df = pd.merge_asof(df, df_pv_temp[merge_cols].drop_duplicates('timestamp'),
                           on='timestamp', direction='nearest', tolerance=pd.Timedelta('2min'))
        matched = df[merge_cols[1]].notna().sum()
        logger.info(f'  Merged: {merge_cols[1:]} | {matched:,}/{len(df):,} ({matched/len(df)*100:.1f}%)')

        if has_irr_pv and had_irr_main and '_irr_main' in df.columns:
            df['Irradiance_Wm2'] = df['_irr_main'].fillna(df['Irradiance_Wm2'])
            n_main = df['_irr_main'].notna().sum()
            n_pv = (df['_irr_main'].isna() & df['Irradiance_Wm2'].notna()).sum()
            df = df.drop(columns=['_irr_main'])
            logger.info(f'  Irradiance: {n_main:,} from .txt + {n_pv:,} from pv_temp')

    df.set_index('timestamp', inplace=True)
    df.index = df.index.tz_localize('Asia/Bangkok').tz_convert('UTC')

    # Step 3: Clean — replace sentinel values with NaN
    logger.info('\n[3/8] Cleaning sentinel values...')

    # -0.999 = sensor communication error → NaN for ALL columns
    df = df.replace(-0.999, np.nan)

    # Column-specific: 0 = sensor error for temp columns (Thailand never 0°C)
    ZERO_IS_ERROR = [C['COL_AMBIENT_TEMP'], C['COL_PV_TEMP']]
    for col in ZERO_IS_ERROR:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
            n_zeros = (df[col] == 0).sum()
            if n_zeros > 0:
                df.loc[df[col] == 0, col] = np.nan
                logger.info(f'  {col}: replaced {n_zeros} zeros with NaN (sensor error)')

    # Physical range filter for temperature sensors
    # Thailand climate: amb temp 15-50°C, PV panel 15-85°C
    TEMP_RANGES = {
        C['COL_AMBIENT_TEMP']: (15.0, 55.0),
        C['COL_PV_TEMP']:      (15.0, 85.0),
    }
    for col, (lo, hi) in TEMP_RANGES.items():
        if col in df.columns:
            out_of_range = ((df[col] < lo) | (df[col] > hi)) & df[col].notna()
            n_bad = out_of_range.sum()
            if n_bad > 0:
                bad_vals = df.loc[out_of_range, col]
                df.loc[out_of_range, col] = np.nan
                logger.info(f'  {col}: replaced {n_bad} out-of-range values with NaN '
                            f'(range {lo}-{hi}°C, found {bad_vals.min():.1f} to {bad_vals.max():.1f}°C)')

    # Note: PV power = 0 at night is REAL, Irradiance = 0 at night is REAL
    # Only -0.999 is error for these columns

    # Critical columns: drop rows where these are NaN
    critical = [C['COL_PV_POWER'], C['COL_PLIM'], C['COL_LOAD']]
    before = len(df)
    df = df.dropna(subset=critical)
    dropped = before - len(df)
    logger.info(f'Critical clean: {before:,} -> {len(df):,} (dropped {dropped:,} = {dropped/max(before,1)*100:.1f}%)')

    # Interpolate non-critical columns (fill small gaps)
    NON_CRIT = [C['COL_AMBIENT_TEMP'], C['COL_IRRADIANCE'], C['COL_PV_TEMP']]
    for col in NON_CRIT:
        if col in df.columns:
            # Ensure numeric (PV temp from CSV merge may be object dtype)
            df[col] = pd.to_numeric(df[col], errors='coerce')
            n = df[col].isna().sum()
            if n > 0:
                df[col] = df[col].interpolate(method='time', limit=4)
                remaining = df[col].isna().sum()
                logger.info(f'  {col}: interpolated {n} -> {remaining} NaN remaining')

    # Report sensor data sources
    for col, label in [(C['COL_AMBIENT_TEMP'], 'Ambient temp'),
                       (C['COL_PV_TEMP'], 'PV temp')]:
        if col in df.columns:
            valid = df[col].notna().sum()
            if valid > 0:
                logger.info(f'  {label} from SENSOR: {valid:,} values ({df[col].min():.1f} – {df[col].max():.1f}°C)')
            else:
                logger.info(f'  {label}: NO valid data')

    logger.info(f'Clean data: {len(df):,} rows')

    # Step 4: Hybrid Filter
    logger.info('\n[4/8] Hybrid Filter...')
    df_pot = df[df[C['COL_PV_POWER']] < (df[C['COL_PLIM']] * 0.95)].copy()
    logger.info(f'Passed: {len(df_pot):,} / {len(df):,} ({len(df_pot)/len(df)*100:.1f}%)')

    # Step 5: Panel Temperature — use SENSOR if available, else NOCT formula
    logger.info('\n[5/8] Panel Temperature...')
    has_pv_temp = C['COL_PV_TEMP'] in df_pot.columns and df_pot[C['COL_PV_TEMP']].notna().sum() > len(df_pot) * 0.3
    if has_pv_temp:
        valid_pv_temp = df_pot[C['COL_PV_TEMP']].notna().sum()
        total_pv_temp = len(df_pot)
        # Use sensor PV temp, fallback to NOCT for gaps
        nf = (C['NOCT'] - 20) / 800.0
        noct_calc = df_pot[C['COL_AMBIENT_TEMP']] + nf * df_pot[C['COL_IRRADIANCE']]
        df_pot['panel_temp'] = df_pot[C['COL_PV_TEMP']].fillna(noct_calc)
        logger.info(f'  Source: PV SENSOR (real) — {valid_pv_temp:,} / {total_pv_temp:,} rows')
        logger.info(f'  NOCT fallback for gaps: {total_pv_temp - valid_pv_temp:,} rows')
        logger.info(f'  Range: {df_pot["panel_temp"].min():.1f} – {df_pot["panel_temp"].max():.1f}°C')
    else:
        nf = (C['NOCT'] - 20) / 800.0
        df_pot['panel_temp'] = df_pot[C['COL_AMBIENT_TEMP']] + nf * df_pot[C['COL_IRRADIANCE']]
        logger.info(f'  Source: NOCT formula (no PV sensor)')
        logger.info(f'  Range: {df_pot["panel_temp"].min():.1f} – {df_pot["panel_temp"].max():.1f}°C')

    # Step 6: Clean BEFORE resample — drop NaN rows per column
    # This ensures averages are computed only from valid data
    logger.info('\n[6/8] Pre-resample cleaning (drop NaN before averaging)...')
    for col_name, col_key in [('PV power', C['COL_PV_POWER']),
                               ('Load', C['COL_LOAD']),
                               ('Irradiance', C['COL_IRRADIANCE']),
                               ('Panel temp', 'panel_temp'),
                               ('Ambient temp', C['COL_AMBIENT_TEMP'])]:
        if col_key in df_pot.columns:
            n = df_pot[col_key].isna().sum()
            if n > 0:
                logger.info(f'  {col_name}: {n:,} NaN will be excluded from 15-min average')

    # Step 7: Resample to 15-min (NaN excluded from mean automatically)
    logger.info('\n[7/8] Resampling to 15-min intervals...')
    pv_15m    = df_pot[C['COL_PV_POWER']].resample('15min').mean().dropna()
    load_15m  = df[C['COL_LOAD']].resample('15min').mean().dropna()
    pt_15m    = df_pot['panel_temp'].resample('15min').mean().dropna()
    irr_15m   = df_pot[C['COL_IRRADIANCE']].resample('15min').mean().dropna()
    amb_15m   = df_pot[C['COL_AMBIENT_TEMP']].resample('15min').mean().dropna()
    logger.info(f'Resampled: {len(pv_15m):,} intervals | {pv_15m.index.min()} to {pv_15m.index.max()}')

    # Step 8: Build output
    logger.info('\n[8/8] Building output...')
    data = []
    for idx, val in pv_15m.items():
        data.append({
            'org_id': C['ORG_ID'], 'site_id': C['SITE_ID'],
            'timestamp': idx.strftime('%Y-%m-%d %H:%M:%S'),
            'avg_pv_power_kw': round(float(val), 2),
            'total_load_kw': round(float(load_15m.get(idx, 0)), 2),
            'avg_irradiance_wm2': int(irr_15m.get(idx, 0)),
            'avg_ambient_temp_c': round(float(amb_15m.get(idx, 0)), 2),
            'avg_panel_temp_c': round(float(pt_15m.get(idx, 0)), 2),
            'data_quality': 'GOOD',
        })
    df_out = pd.DataFrame(data)
    out_path = os.path.join(C['OUTPUT_DIR'], 'cloud_metering_history_output.csv')
    df_out.to_csv(out_path, index=False)
    logger.info(f'Saved: {out_path} ({len(df_out):,} rows)')

    # Daily summary
    df_out['date'] = pd.to_datetime(df_out['timestamp']).dt.date
    daily = df_out.groupby('date').agg(
        rows=('timestamp','count'), avg_pv=('avg_pv_power_kw','mean'),
        max_pv=('avg_pv_power_kw','max'), avg_load=('total_load_kw','mean'),
    ).round(2)
    daily.to_csv(os.path.join(C['OUTPUT_DIR'], 'block1_daily_summary.csv'))

    logger.info(f'\nBlock 1 DONE: {len(df_out):,} rows, {len(daily)} days')
    return df_out


# ============================================================
# Fetch Weather from REAL Open-Meteo API
# ============================================================
def fetch_weather_api(cfg, start_date, end_date):
    """
    Fetch from REAL Open-Meteo Archive API.
    Uses 'hourly' endpoint (minutely_15 not available for archive).
    Resamples hourly → 15-min via interpolation to match Block 1 data.
    Returns DataFrame with ghi_api_wm2, temp_c, cloud_cover_pct.
    """
    logger.info('\n--- Fetching weather from Open-Meteo Archive API ---')
    logger.info(f'  URL: https://archive-api.open-meteo.com/v1/archive')
    logger.info(f'  Location: {cfg["LAT"]}, {cfg["LON"]}')
    logger.info(f'  Period: {start_date} to {end_date}')

    try:
        res = requests.get(
            'https://archive-api.open-meteo.com/v1/archive',
            params={
                'latitude': cfg['LAT'],
                'longitude': cfg['LON'],
                'start_date': start_date,
                'end_date': end_date,
                'hourly': 'shortwave_radiation,temperature_2m,cloud_cover',
                'timezone': 'GMT',
            },
            timeout=120
        )
        res.raise_for_status()
        data = res.json()
    except requests.exceptions.ConnectionError as e:
        logger.error(f'  CONNECTION FAILED: {e}')
        logger.error(f'  Make sure this machine can access the internet!')
        return None
    except Exception as e:
        logger.error(f'  API FAILED: {e}')
        return None

    hourly = data.get('hourly', {})
    required = ['shortwave_radiation', 'temperature_2m', 'cloud_cover', 'time']
    missing = [k for k in required if k not in hourly]
    if missing:
        logger.error(f'  API response missing: {missing}')
        logger.error(f'  Available keys: {list(hourly.keys()) if hourly else list(data.keys())}')
        return None

    df = pd.DataFrame({
        'timestamp': pd.to_datetime(hourly['time'], utc=True),
        'ghi_api_wm2': hourly['shortwave_radiation'],
        'temp_c': hourly['temperature_2m'],
        'cloud_cover_pct': hourly['cloud_cover'],
    }).set_index('timestamp')

    logger.info(f'  API hourly rows: {len(df):,}')

    # Resample hourly → 15-min via interpolation (to match Block 1 15-min data)
    df = df.resample('15min').interpolate(method='time')
    logger.info(f'  After 15-min interpolation: {len(df):,} rows')

    logger.info(f'  GHI range: {df["ghi_api_wm2"].min():.0f} – {df["ghi_api_wm2"].max():.0f} W/m²')
    logger.info(f'  Temp range: {df["temp_c"].min():.1f} – {df["temp_c"].max():.1f}°C')
    logger.info(f'  Cloud range: {df["cloud_cover_pct"].min():.0f} – {df["cloud_cover_pct"].max():.0f}%')
    logger.info(f'  API connection: SUCCESS')

    return df


# ============================================================
# BLOCK 2: Train Solar Model
# ============================================================
def run_block2(cfg, df_db, df_weather):
    logger.info('\n' + '=' * 60)
    logger.info('BLOCK 2: Solar Model Training')
    logger.info(f'Features: {len(SOLAR_FEATURES)} | NOCT: {cfg["NOCT"]} | KWP: {cfg["KWP"]}')
    logger.info('=' * 60)

    # Feature engineering
    logger.info('\n[1/6] Feature engineering...')
    df = df_db.join(df_weather, how='inner')
    df = df.dropna(subset=['avg_pv_power_kw', 'ghi_api_wm2', 'temp_c'])

    if df.empty:
        logger.error('ABORT: No overlapping timestamps between DB and API')
        return None

    site_loc = pvlib.location.Location(cfg['LAT'], cfg['LON'], tz='UTC')
    cs = site_loc.get_clearsky(df.index)['ghi']
    df['ghi_clearsky'] = cs
    df['kt'] = (df['ghi_api_wm2'] / (cs + 1e-6)).clip(upper=1.2)
    df['hour'] = df.index.hour
    df['month'] = df.index.month
    df['solar_lag_24h'] = df['avg_pv_power_kw'].shift(96)

    nf = (cfg['NOCT'] - 20) / 800.0
    if 'avg_panel_temp_c' in df.columns and df['avg_panel_temp_c'].notna().sum() > len(df) * 0.5:
        # Use REAL sensor PV temp (best quality), NOCT fallback for gaps
        df['panel_temp'] = df['avg_panel_temp_c'].fillna(
            df['temp_c'] + nf * df['ghi_api_wm2'])
        sensor_pct = df['avg_panel_temp_c'].notna().sum() / len(df) * 100
        logger.info(f'Panel Temp: PV SENSOR (real) {sensor_pct:.0f}% + NOCT fallback for gaps')
    else:
        df['panel_temp'] = df['temp_c'] + nf * df['ghi_api_wm2']
        logger.info('Panel Temp: Calculated from API (NOCT formula — no PV sensor data)')

    df = df[cs > 10].dropna(subset=SOLAR_FEATURES + ['avg_pv_power_kw'])
    logger.info(f'Training samples: {len(df):,}')

    if len(df) < MIN_ROWS:
        logger.error(f'ABORT: Only {len(df)} rows (need {MIN_ROWS})')
        return None

    # PSI
    logger.info('\n[2/6] PSI drift check...')
    mid = len(df) // 2
    drift = []
    for feat in SOLAR_FEATURES:
        psi = calc_psi(df[feat].iloc[:mid], df[feat].iloc[mid:])
        st = 'STABLE' if psi < 0.1 else 'MODERATE' if psi < 0.25 else 'DRIFT!'
        logger.info(f'  {feat:20s}: PSI={psi:.4f} [{st}]')
        if st == 'DRIFT!': drift.append(feat)

    # Train
    logger.info('\n[3/6] Training 5-Fold CV...')
    X = df[SOLAR_FEATURES]
    y = df['avg_pv_power_kw'].clip(upper=cfg['KWP'])
    tscv = TimeSeriesSplit(n_splits=5)
    mapes, rmses = [], []

    for fold, (ti, vi) in enumerate(tscv.split(X)):
        logger.info(f'  Fold {fold+1}: Train={len(ti):,} | Val={len(vi):,}')
        m = lgb.LGBMRegressor(n_estimators=1000, learning_rate=0.03, num_leaves=45,
                               max_depth=9, min_data_in_leaf=30,
                               feature_fraction=0.85, bagging_fraction=0.85, verbose=-1)
        m.fit(X.iloc[ti], y.iloc[ti], eval_set=[(X.iloc[vi], y.iloc[vi])],
              callbacks=[lgb.early_stopping(50, verbose=False)])
        yp = np.clip(m.predict(X.iloc[vi]), 0, cfg['KWP'])
        mask = y.iloc[vi] > (cfg['KWP'] * 0.05)
        if mask.sum() > 0:
            fm = mean_absolute_percentage_error(y.iloc[vi][mask], yp[mask]) * 100
            fr = np.sqrt(mean_squared_error(y.iloc[vi][mask], yp[mask]))
        else:
            fm, fr = 0, 0
        mapes.append(fm); rmses.append(fr)
        logger.info(f'  Fold {fold+1}: MAPE={fm:.2f}% | RMSE={fr:.2f} kW')

    avg_mape = np.mean(mapes)
    avg_rmse = np.mean(rmses)
    logger.info(f'\n  >>> Solar Average MAPE: {avg_mape:.2f}% | RMSE: {avg_rmse:.2f} kW')

    # Final model
    logger.info('\n[4/6] Final model...')
    final = lgb.LGBMRegressor(n_estimators=1000, learning_rate=0.03, num_leaves=45,
                               max_depth=9, min_data_in_leaf=30,
                               feature_fraction=0.85, bagging_fraction=0.85, verbose=-1)
    final.fit(X, y)

    # Importance
    logger.info('\n[5/6] Feature importance:')
    imp = final.feature_importances_
    ti = sum(imp)
    imp_dict = {}
    for n, v in sorted(zip(SOLAR_FEATURES, imp), key=lambda x: -x[1]):
        p = v / ti * 100
        imp_dict[n] = round(p, 2)
        logger.info(f'    {n:20s}: {p:5.1f}% {'+'*int(p/2)}')

    # Save
    logger.info('\n[6/6] Saving...')
    model_path = os.path.join(cfg['OUTPUT_DIR'], f'solar_model_{cfg["SITE_ID"]}_v2.2_prod.txt')
    meta_path = model_path.replace('.txt', '_meta.json')
    final.booster_.save_model(model_path)

    meta = {
        'model_name': 'SOLAR_LGBM', 'model_version': 'v2.2_prod',
        'site_id': cfg['SITE_ID'], 'features': SOLAR_FEATURES,
        'feature_hash': feature_hash(SOLAR_FEATURES),
        'noct': cfg['NOCT'], 'installed_kwp': cfg['KWP'],
        'avg_mape': round(avg_mape, 2), 'avg_rmse': round(avg_rmse, 2),
        'fold_mapes': [round(m, 2) for m in mapes],
        'training_samples': len(X),
        'date_range': f'{df.index.min()} to {df.index.max()}',
        'trained_at': datetime.now(timezone.utc).isoformat(),
        'feature_importance': imp_dict,
        'drift_alerts': drift,
        'weather_source': 'Site sensors (GHI+temp) + API cloud_cover',
    }
    with open(meta_path, 'w') as f:
        json.dump(meta, f, indent=2, default=str)

    logger.info(f'  Model: {model_path} ({os.path.getsize(model_path)/1024:.1f} KB)')
    logger.info(f'  Meta:  {meta_path}')
    return meta


# ============================================================
# BLOCK 3: Train Load Model
# ============================================================
def run_block3(cfg, df_db, df_weather):
    logger.info('\n' + '=' * 60)
    logger.info('BLOCK 3: Load Model Training')
    logger.info(f'Features: {len(LOAD_FEATURES)} | Leaves: 63 | Depth: 10')
    logger.info('=' * 60)

    logger.info('\n[1/6] Feature engineering...')
    df = df_db.join(df_weather[['temp_c']], how='inner')
    df = df.dropna(subset=['total_load_kw', 'temp_c'])

    df['hour'] = df.index.hour
    df['month'] = df.index.month
    df['dayofweek'] = df.index.dayofweek
    df['is_weekend'] = (df['dayofweek'] >= 5).astype(int)
    df['load_lag_24h'] = df['total_load_kw'].shift(96)
    df['load_lag_7d'] = df['total_load_kw'].shift(672)
    df = df.dropna(subset=LOAD_FEATURES + ['total_load_kw'])

    logger.info(f'Training samples: {len(df):,}')
    logger.info(f'Load: {df["total_load_kw"].min():.1f} – {df["total_load_kw"].max():.1f} kW')
    logger.info(f'Weekend: {df["is_weekend"].mean()*100:.1f}% | Days: {(df.index.max()-df.index.min()).days}')

    if len(df) < MIN_ROWS:
        logger.error(f'ABORT: Only {len(df)} rows'); return None

    # PSI
    logger.info('\n[2/6] PSI drift check...')
    mid = len(df) // 2
    drift = []
    for feat in LOAD_FEATURES:
        if df[feat].nunique() > 2:
            psi = calc_psi(df[feat].iloc[:mid], df[feat].iloc[mid:])
            st = 'STABLE' if psi < 0.1 else 'MODERATE' if psi < 0.25 else 'DRIFT!'
            logger.info(f'  {feat:20s}: PSI={psi:.4f} [{st}]')
            if st == 'DRIFT!': drift.append(feat)

    # Train
    logger.info('\n[3/6] Training 5-Fold CV...')
    X = df[LOAD_FEATURES]
    y = df['total_load_kw']
    tscv = TimeSeriesSplit(n_splits=5)
    mapes, rmses = [], []

    for fold, (ti, vi) in enumerate(tscv.split(X)):
        logger.info(f'  Fold {fold+1}: Train={len(ti):,} | Val={len(vi):,}')
        m = lgb.LGBMRegressor(n_estimators=1200, learning_rate=0.02, num_leaves=63,
                               max_depth=10, min_data_in_leaf=40,
                               feature_fraction=0.8, bagging_fraction=0.8, verbose=-1)
        m.fit(X.iloc[ti], y.iloc[ti], eval_set=[(X.iloc[vi], y.iloc[vi])],
              callbacks=[lgb.early_stopping(50, verbose=False)])
        yp = np.clip(m.predict(X.iloc[vi]), 0, None)
        mask = y.iloc[vi] > 1.0
        if mask.sum() > 0:
            fm = mean_absolute_percentage_error(y.iloc[vi][mask], yp[mask]) * 100
            fr = np.sqrt(mean_squared_error(y.iloc[vi][mask], yp[mask]))
        else:
            fm, fr = 0, 0
        mapes.append(fm); rmses.append(fr)
        logger.info(f'  Fold {fold+1}: MAPE={fm:.2f}% | RMSE={fr:.2f} kW')

    avg_mape = np.mean(mapes)
    avg_rmse = np.mean(rmses)
    logger.info(f'\n  >>> Load Average MAPE: {avg_mape:.2f}% | RMSE: {avg_rmse:.2f} kW')

    # Final
    logger.info('\n[4/6] Final model...')
    final = lgb.LGBMRegressor(n_estimators=1200, learning_rate=0.02, num_leaves=63,
                               max_depth=10, min_data_in_leaf=40,
                               feature_fraction=0.8, bagging_fraction=0.8, verbose=-1)
    final.fit(X, y)

    logger.info('\n[5/6] Feature importance:')
    imp = final.feature_importances_
    ti = sum(imp)
    imp_dict = {}
    for n, v in sorted(zip(LOAD_FEATURES, imp), key=lambda x: -x[1]):
        p = v / ti * 100
        imp_dict[n] = round(p, 2)
        logger.info(f'    {n:20s}: {p:5.1f}% {'+'*int(p/2)}')

    logger.info('\n[6/6] Saving...')
    model_path = os.path.join(cfg['OUTPUT_DIR'], f'load_model_{cfg["SITE_ID"]}_v2.2_prod.txt')
    meta_path = model_path.replace('.txt', '_meta.json')
    final.booster_.save_model(model_path)

    meta = {
        'model_name': 'LOAD_LGBM', 'model_version': 'v2.2_prod',
        'site_id': cfg['SITE_ID'], 'features': LOAD_FEATURES,
        'feature_hash': feature_hash(LOAD_FEATURES),
        'hyperparameters': {'n_estimators': 1200, 'learning_rate': 0.02,
                            'num_leaves': 63, 'max_depth': 10},
        'avg_mape': round(avg_mape, 2), 'avg_rmse': round(avg_rmse, 2),
        'fold_mapes': [round(m, 2) for m in mapes],
        'training_samples': len(X),
        'date_range': f'{df.index.min()} to {df.index.max()}',
        'trained_at': datetime.now(timezone.utc).isoformat(),
        'feature_importance': imp_dict,
        'drift_alerts': drift,
        'weather_source': 'Site sensors (GHI+temp) + API cloud_cover',
    }
    with open(meta_path, 'w') as f:
        json.dump(meta, f, indent=2, default=str)

    logger.info(f'  Model: {model_path} ({os.path.getsize(model_path)/1024:.1f} KB)')
    return meta


# ============================================================
# SAVE TO DATABASE
# ============================================================
def save_to_db(cfg, df_out):
    """
    บันทึก df_out จาก Block 1 ลง cloud_metering_history
    - ใช้ INSERT IGNORE เพื่อข้าม row ที่ซ้ำ (Primary Key: org_id + site_id + timestamp)
    - timestamp ใน df_out เป็น UTC string → แปลงเป็น Asia/Bangkok ก่อน insert
      เพราะ DB เก็บเป็น datetime (ไม่มี tz) และระบบอ่านด้วย TIMEZONE Asia/Bangkok
    - batch_size=500 เพื่อไม่ให้ query ยาวเกินไป
    """
    logger.info('\n' + '=' * 60)
    logger.info('SAVING TO DATABASE: cloud_metering_history')
    logger.info('=' * 60)

    # แปลง column ให้ตรงกับ schema ของ DB
    col_map = {
        'avg_pv_power_kw':    'pv_power_kw',
        'total_load_kw':      'load_power_kw',
        'avg_irradiance_wm2': 'irradiance_wm2',
        'avg_ambient_temp_c': 'ambient_temp_c',
        'avg_panel_temp_c':   'panel_temp_c',
    }
    df = df_out.rename(columns=col_map).copy()

    # timestamp: UTC string → Bangkok datetime (naive) สำหรับ MySQL datetime column
    df['timestamp'] = (
        pd.to_datetime(df['timestamp'], utc=True)
          .dt.tz_convert('Asia/Bangkok')
          .dt.tz_localize(None)   # drop tz info → naive datetime
    )

    # columns ที่จะ insert (ตรงกับ DB schema)
    DB_COLS = [
        'org_id', 'site_id', 'timestamp',
        'pv_power_kw', 'load_power_kw',
        'irradiance_wm2', 'ambient_temp_c', 'panel_temp_c',
        'sample_count', 'data_quality',
    ]

    # เติม columns ที่ไม่มีใน df_out
    if 'sample_count' not in df.columns:
        df['sample_count'] = 1
    if 'data_quality' not in df.columns:
        df['data_quality'] = 'GOOD'

    # ตรวจสอบว่ามี column ครบ
    missing = [c for c in DB_COLS if c not in df.columns]
    if missing:
        logger.error(f'Missing columns: {missing}')
        return False

    insert_sql = """
        INSERT IGNORE INTO cloud_metering_history
            (org_id, site_id, timestamp,
             pv_power_kw, load_power_kw,
             irradiance_wm2, ambient_temp_c, panel_temp_c,
             sample_count, data_quality)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        logger.info(f'  DB connection: SUCCESS')
    except mysql.connector.Error as e:
        logger.error(f'  DB connection FAILED: {e}')
        logger.error(f'  ตรวจสอบ DB_CONFIG และให้ MySQL server รันอยู่')
        return False

    total = len(df)
    inserted = 0
    skipped = 0
    batch_size = 500
    rows_buffer = []

    for _, row in df[DB_COLS].iterrows():
        rows_buffer.append((
            str(row['org_id']),
            str(row['site_id']),
            row['timestamp'],                                         # datetime object
            float(row['pv_power_kw'])   if pd.notna(row['pv_power_kw'])   else None,
            float(row['load_power_kw']) if pd.notna(row['load_power_kw']) else None,
            int(row['irradiance_wm2'])  if pd.notna(row['irradiance_wm2']) else None,
            float(row['ambient_temp_c']) if pd.notna(row['ambient_temp_c']) else None,
            float(row['panel_temp_c'])  if pd.notna(row['panel_temp_c'])  else None,
            int(row['sample_count']),
            str(row['data_quality']),
        ))

        if len(rows_buffer) >= batch_size:
            cursor.executemany(insert_sql, rows_buffer)
            conn.commit()
            inserted += cursor.rowcount
            skipped  += (len(rows_buffer) - cursor.rowcount)
            rows_buffer = []
            pct = (inserted + skipped) / total * 100
            logger.info(f'  Progress: {inserted + skipped:,}/{total:,} ({pct:.0f}%) '
                        f'| inserted={inserted:,} skipped={skipped:,}')

    # flush ที่เหลือ
    if rows_buffer:
        cursor.executemany(insert_sql, rows_buffer)
        conn.commit()
        inserted += cursor.rowcount
        skipped  += (len(rows_buffer) - cursor.rowcount)

    cursor.close()
    conn.close()

    logger.info(f'\n  INSERT สำเร็จ : {inserted:,} rows')
    logger.info(f'  ข้ามซ้ำ (IGNORE): {skipped:,} rows')
    logger.info(f'  รวมทั้งหมด    : {total:,} rows')
    logger.info(f'  DB save: COMPLETE')
    return True


# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser(description='GridMind AI Block 1+2+3 Runner')
    parser.add_argument('--data-dir', type=str, help='Directory with .txt files')
    parser.add_argument('--output-dir', type=str, help='Output directory')
    parser.add_argument('--pattern', type=str, help='File pattern (default: 20*.txt)')
    parser.add_argument('--lat', type=float, help='Site latitude')
    parser.add_argument('--lon', type=float, help='Site longitude')
    parser.add_argument('--kwp', type=float, help='Installed kWp')
    parser.add_argument('--noct', type=int, help='NOCT value')
    parser.add_argument('--site-id', type=str)
    parser.add_argument('--org-id', type=str)
    parser.add_argument('--col-timestamp', type=str)
    parser.add_argument('--col-pv', type=str)
    parser.add_argument('--col-plim', type=str)
    parser.add_argument('--col-load', type=str)
    parser.add_argument('--col-temp', type=str)
    parser.add_argument('--col-irr', type=str)
    args = parser.parse_args()

    cfg = get_config(args)
    os.makedirs(cfg['OUTPUT_DIR'], exist_ok=True)

    logger.info('=' * 60)
    logger.info('GridMind AI V2.2 — Complete Pipeline (Block 1+2+3)')
    logger.info('=' * 60)
    logger.info(f'Site:     {cfg["SITE_ID"]}')
    logger.info(f'Location: {cfg["LAT"]}, {cfg["LON"]}')
    logger.info(f'System:   {cfg["KWP"]} kWp | NOCT: {cfg["NOCT"]}')
    logger.info(f'Data:     {cfg["DATA_DIR"]}/{cfg["PATTERN"]}')
    logger.info(f'Output:   {cfg["OUTPUT_DIR"]}/')

    # ===== BLOCK 1 =====
    df_out = run_block1(cfg)

    # ===== SAVE TO DB =====
    save_to_db(cfg, df_out)

    # ===== BUILD WEATHER: Site sensors + API cloud_cover =====
    logger.info('\n' + '=' * 60)
    logger.info('Building weather features (SITE SENSORS + API cloud_cover)')
    logger.info('=' * 60)

    df_db = pd.read_csv(os.path.join(cfg['OUTPUT_DIR'], 'cloud_metering_history_output.csv'))
    df_db['timestamp'] = pd.to_datetime(df_db['timestamp'], utc=True)
    df_db.set_index('timestamp', inplace=True)

    # Site sensor data already in df_db from Block 1
    logger.info(f'\n  Site sensor data from Block 1:')
    logger.info(f'    Irradiance (pyranometer): {df_db["avg_irradiance_wm2"].min():.0f} – {df_db["avg_irradiance_wm2"].max():.0f} W/m2')
    logger.info(f'    Ambient temp (sensor):    {df_db["avg_ambient_temp_c"].min():.1f} – {df_db["avg_ambient_temp_c"].max():.1f} C')
    logger.info(f'    Panel temp (PV sensor):   {df_db["avg_panel_temp_c"].min():.1f} – {df_db["avg_panel_temp_c"].max():.1f} C')

    # Fetch API only for cloud_cover (site has no cloud sensor)
    start = df_db.index.min().strftime('%Y-%m-%d')
    end = df_db.index.max().strftime('%Y-%m-%d')

    df_api = fetch_weather_api(cfg, start, end)

    # Build weather DataFrame: site sensors + API cloud_cover
    df_weather = pd.DataFrame(index=df_db.index)
    df_weather['ghi_api_wm2'] = df_db['avg_irradiance_wm2'].astype(float)   # FROM SITE SENSOR
    df_weather['temp_c'] = df_db['avg_ambient_temp_c'].astype(float)          # FROM SITE SENSOR

    if df_api is not None and 'cloud_cover_pct' in df_api.columns:
        # cloud_cover from API (site has no cloud sensor)
        df_weather['cloud_cover_pct'] = df_api['cloud_cover_pct'].reindex(
            df_weather.index, method='nearest', tolerance='30min')
        api_filled = df_weather['cloud_cover_pct'].notna().sum()
        logger.info(f'    Cloud cover (API):        {api_filled:,} / {len(df_weather):,} matched')
    else:
        # Fallback: estimate cloud_cover from irradiance
        logger.warning('    API failed — estimating cloud_cover from site irradiance')
        df_weather['cloud_cover_pct'] = np.clip(100 - (df_weather['ghi_api_wm2'] / 12), 0, 100)

    # Fill any remaining NaN in cloud_cover
    df_weather['cloud_cover_pct'] = df_weather['cloud_cover_pct'].fillna(50)

    logger.info(f'\n  Weather features summary:')
    logger.info(f'    GHI source:        SITE PYRANOMETER (real, 15-min)')
    logger.info(f'    Temperature source: SITE SENSOR (real, 15-min)')
    logger.info(f'    Cloud cover source: {"API (hourly interpolated)" if df_api is not None else "Estimated from irradiance"}')
    logger.info(f'    Total rows: {len(df_weather):,}')

    # Save for reference
    df_weather.to_csv(os.path.join(cfg['OUTPUT_DIR'], 'weather_site_sensors.csv'))
    if df_api is not None:
        df_api.to_csv(os.path.join(cfg['OUTPUT_DIR'], 'weather_from_api.csv'))
    logger.info(f'  Saved: weather_site_sensors.csv')

    # ===== BLOCK 2 =====
    solar_meta = run_block2(cfg, df_db, df_weather)

    # ===== BLOCK 3 =====
    load_meta = run_block3(cfg, df_db, df_weather)

    # ===== FINAL SUMMARY =====
    logger.info('\n' + '=' * 60)
    logger.info('PIPELINE COMPLETE')
    logger.info('=' * 60)

    if solar_meta:
        logger.info(f'\nSolar Model:')
        logger.info(f'  MAPE: {solar_meta["avg_mape"]:.2f}% | RMSE: {solar_meta["avg_rmse"]:.2f} kW')
        logger.info(f'  Samples: {solar_meta["training_samples"]:,}')
        logger.info(f'  Weather: {solar_meta["weather_source"]}')

    if load_meta:
        logger.info(f'\nLoad Model:')
        logger.info(f'  MAPE: {load_meta["avg_mape"]:.2f}% | RMSE: {load_meta["avg_rmse"]:.2f} kW')
        logger.info(f'  Samples: {load_meta["training_samples"]:,}')

    logger.info(f'\nOutput files:')
    for f in sorted(os.listdir(cfg['OUTPUT_DIR'])):
        sz = os.path.getsize(os.path.join(cfg['OUTPUT_DIR'], f)) / 1024
        logger.info(f'  {f} ({sz:.1f} KB)')

    logger.info(f'\nLog saved: block123_run.log')
    logger.info(f'Next step: deploy models to Edge server')


if __name__ == '__main__':
    main()