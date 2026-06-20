#!/usr/bin/env python3
"""
GridMind AI V2.2 — BCP 2026 Importer → metering_history (1-min)
=================================================================
อ่านไฟล์ bcp_Ld_Gtp_temp_YYYYMM.csv + bcp_PVtemp_Irr_YYYYMM.csv
→ Clean → Resample 1 นาที → บันทึกลง solar_edge.metering_history

Column mapping (2026 format → metering_history schema):
    bcp_Ld_Gtp_temp:   GTP1_Total_Power_kW → pv_power_kw
                       Aload_SumkW         → load_power_kw
                       an5                 → ambient_temp_c
                       plim_gtp_1 / 100    → plim_gtp_1
    bcp_PVtemp_Irr:    an3                 → panel_temp_c
                       Irr_W_m2            → irradiance_wm2
                       Irrt_kWh_m2         → irrt_kwh_m2

ไม่มีในไฟล์ → NULL:
    batt_power_kw, grid_import_kw, grid_export_kw,
    genset_power_kw, batt_soc, grid_voltage_avg_v,
    grid_frequency_hz, fuel_level_pct

Requirements:
    pip install pandas numpy mysql-connector-python

Usage:
    python3 import_bcp2026_metering.py                        # อ่านทุกไฟล์ใน folder ปัจจุบัน
    python3 import_bcp2026_metering.py --data-dir /path/data  # ระบุ folder
    python3 import_bcp2026_metering.py --dry-run              # ทดสอบโดยไม่ insert DB
"""
import pandas as pd
import numpy as np
import os
import sys
import glob
import logging
import warnings
import argparse

warnings.filterwarnings('ignore')
import mysql.connector

# ============================================================
# Edge DB Config
# ============================================================
EDGE_DB_CONFIG = {
    'host':     'localhost',
    'database': 'solar_edge',
    'user':     'root',
    'password': '',   # <-- ใส่รหัสผ่าน
}

# ============================================================
# Logging
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(open(sys.stdout.fileno(), mode='w', encoding='utf-8', closefd=False)),
        logging.FileHandler('import_bcp2026_metering.log', mode='w', encoding='utf-8'),
    ]
)
logger = logging.getLogger('gridmind')


# ============================================================
# STEP 1: Load bcp_Ld_Gtp_temp_*.csv
# ============================================================
def load_main_files(data_dir):
    files = sorted(glob.glob(os.path.join(data_dir, 'bcp_Ld_Gtp_temp_*.csv')))
    if not files:
        logger.error(f'No bcp_Ld_Gtp_temp_*.csv files found in {data_dir}')
        sys.exit(1)

    logger.info(f'\n[1/5] Loading {len(files)} main file(s):')
    dfs = []
    for f in files:
        try:
            chunk = pd.read_csv(f, encoding='utf-8-sig')

            # PLIM auto-scale: ถ้า >500 หาร 100 เหมือน block1
            if 'plim_gtp_1' in chunk.columns:
                pv = pd.to_numeric(chunk['plim_gtp_1'], errors='coerce').replace(-0.999, np.nan).dropna()
                plim_info = ''
                if len(pv) > 0 and pv.max() > 500:
                    chunk['plim_gtp_1'] = pd.to_numeric(chunk['plim_gtp_1'], errors='coerce') / 100.0
                    plim_info = ' PLIM/100'

            dfs.append(chunk)
            logger.info(f'  {os.path.basename(f)}: {len(chunk):,} rows{plim_info}')
        except Exception as e:
            logger.warning(f'  Failed {f}: {e}')

    if not dfs:
        logger.error('No main data loaded')
        sys.exit(1)

    df = pd.concat(dfs, ignore_index=True)
    logger.info(f'  Total: {len(df):,} rows')
    return df


# ============================================================
# STEP 2: Load bcp_PVtemp_Irr_*.csv
# ============================================================
def load_pvtemp_files(data_dir):
    files = sorted(glob.glob(os.path.join(data_dir, 'bcp_PVtemp_Irr_*.csv')))
    if not files:
        logger.warning('  No bcp_PVtemp_Irr_*.csv files found — panel_temp/irradiance will be NULL')
        return None

    logger.info(f'\n[2/5] Loading {len(files)} PVtemp/Irr file(s):')
    dfs = []
    for f in files:
        try:
            chunk = pd.read_csv(f, encoding='utf-8-sig')
            dfs.append(chunk)
            logger.info(f'  {os.path.basename(f)}: {len(chunk):,} rows')
        except Exception as e:
            logger.warning(f'  Failed {f}: {e}')

    if not dfs:
        return None

    df_pv = pd.concat(dfs, ignore_index=True)
    df_pv['timestamp'] = pd.to_datetime(df_pv['DatetimeServer'], errors='coerce')
    df_pv = df_pv.dropna(subset=['timestamp'])
    df_pv = df_pv.rename(columns={
        'an3':         'panel_temp_c',
        'Irr_W_m2':    'irradiance_wm2',
        'Irrt_kWh_m2': 'irrt_kwh_m2',
    })

    logger.info(f'  PVtemp/Irr total: {len(df_pv):,} rows | '
                f'{df_pv["timestamp"].min()} to {df_pv["timestamp"].max()}')
    return df_pv


# ============================================================
# STEP 3: Parse timestamp + Merge + Set index UTC
# ============================================================
def parse_and_merge(df_main, df_pv):
    logger.info('\n[3/5] Parse timestamp & merge...')

    # Timestamp ไฟล์ 2026 เป็น ISO (Y-M-D H:M:S)
    df_main['timestamp'] = pd.to_datetime(df_main['DatetimeServer'], errors='coerce')
    before = len(df_main)
    df_main = df_main.dropna(subset=['timestamp'])
    logger.info(f'  Main: {len(df_main):,} / {before:,} (lost {before - len(df_main)})')

    # Rename → ชื่อกลาง
    df_main = df_main.rename(columns={
        'GTP1_Total_Power_kW': 'pv_power_kw',
        'Aload_SumkW':         'load_power_kw',
        'an5':                 'ambient_temp_c',
    })

    # Merge PVtemp/Irr (tolerance 2 นาที)
    if df_pv is not None:
        df_main = df_main.sort_values('timestamp')
        df_pv   = df_pv.sort_values('timestamp')

        pv_cols = ['timestamp']
        for col in ['panel_temp_c', 'irradiance_wm2', 'irrt_kwh_m2']:
            if col in df_pv.columns:
                pv_cols.append(col)

        df_main = pd.merge_asof(
            df_main,
            df_pv[pv_cols].drop_duplicates('timestamp'),
            on='timestamp',
            direction='nearest',
            tolerance=pd.Timedelta('2min')
        )
        matched = df_main['irradiance_wm2'].notna().sum()
        logger.info(f'  Merged PVtemp/Irr: {matched:,}/{len(df_main):,} ({matched/len(df_main)*100:.1f}%)')

    # Set index → UTC
    df_main = df_main.set_index('timestamp')
    df_main.index = df_main.index.tz_localize('Asia/Bangkok').tz_convert('UTC')
    return df_main


# ============================================================
# STEP 3b: Clean
# ============================================================
def clean_data(df):
    logger.info('\n  Cleaning...')

    # -0.999 = sensor error
    df = df.replace(-0.999, np.nan)

    # อุณหภูมิ 0 = sensor error
    for col in ['ambient_temp_c', 'panel_temp_c']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
            n = (df[col] == 0).sum()
            if n > 0:
                df.loc[df[col] == 0, col] = np.nan
                logger.info(f'  {col}: replaced {n:,} zeros with NaN')

    # Physical range filter
    TEMP_RANGES = {
        'ambient_temp_c': (15.0, 55.0),
        'panel_temp_c':   (15.0, 85.0),
    }
    for col, (lo, hi) in TEMP_RANGES.items():
        if col in df.columns:
            bad = ((df[col] < lo) | (df[col] > hi)) & df[col].notna()
            n = bad.sum()
            if n > 0:
                df.loc[bad, col] = np.nan
                logger.info(f'  {col}: replaced {n:,} out-of-range (valid: {lo}-{hi} degC)')

    # Critical drop
    critical = ['pv_power_kw', 'load_power_kw']
    before = len(df)
    df = df.dropna(subset=critical)
    logger.info(f'  Critical drop: {before:,} -> {len(df):,} (dropped {before - len(df):,})')

    # Interpolate non-critical (สูงสุด 4 จุด)
    for col in ['ambient_temp_c', 'irradiance_wm2', 'panel_temp_c', 'irrt_kwh_m2']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
            n = df[col].isna().sum()
            if n > 0:
                df[col] = df[col].interpolate(method='time', limit=4)
                remaining = df[col].isna().sum()
                logger.info(f'  {col}: interpolated {n:,} -> {remaining:,} NaN remaining')

    logger.info(f'  Clean data: {len(df):,} rows')
    return df


# ============================================================
# STEP 4: Resample เป็น 1 นาที + Panel temp
# ============================================================
def resample_1min(df, noct=45):
    logger.info('\n[4/5] Resampling to 1-min intervals...')

    # Panel temp: sensor ถ้ามี ไม่งั้น NOCT
    nf = (noct - 20) / 800.0
    has_sensor = ('panel_temp_c' in df.columns and
                  df['panel_temp_c'].notna().sum() > len(df) * 0.3)
    if has_sensor:
        noct_calc = df['ambient_temp_c'] + nf * df['irradiance_wm2'].fillna(0)
        df['panel_temp_c'] = df['panel_temp_c'].fillna(noct_calc)
        logger.info(f'  Panel temp: PV SENSOR + NOCT fallback')
    else:
        df['panel_temp_c'] = df['ambient_temp_c'] + nf * df['irradiance_wm2'].fillna(0)
        logger.info(f'  Panel temp: NOCT formula')

    RESAMPLE_COLS = {
        'pv_power_kw':    'mean',
        'load_power_kw':  'mean',
        'irradiance_wm2': 'mean',
        'ambient_temp_c': 'mean',
        'panel_temp_c':   'mean',
        'irrt_kwh_m2':    'mean',
        'plim_gtp_1':     'mean',
    }
    RESAMPLE_COLS = {k: v for k, v in RESAMPLE_COLS.items() if k in df.columns}

    df_1m = df[list(RESAMPLE_COLS.keys())].resample('1min').agg(RESAMPLE_COLS)
    df_1m = df_1m.dropna(subset=['pv_power_kw', 'load_power_kw'])

    logger.info(f'  Resampled: {len(df_1m):,} intervals')
    logger.info(f'  Range: {df_1m.index.min()} to {df_1m.index.max()}')
    return df_1m


# ============================================================
# STEP 5: Insert ลง metering_history
# ============================================================
def save_to_metering_history(df_1m, dry_run=False, output_dir='./gridmind_output'):
    logger.info('\n[5/5] Save to Edge DB (solar_edge.metering_history)...')

    # แปลง UTC → Bangkok naive datetime
    df = df_1m.copy().reset_index()
    df['timestamp'] = (
        df['timestamp']
        .dt.tz_convert('Asia/Bangkok')
        .dt.tz_localize(None)
    )

    # ทุก column ของ metering_history ตาม schema — ที่ไม่มีในไฟล์ → None (NULL)
    ALL_COLS = [
        'timestamp',
        'pv_power_kw',        # มี
        'load_power_kw',      # มี
        'batt_power_kw',      # NULL
        'grid_import_kw',     # NULL
        'grid_export_kw',     # NULL
        'genset_power_kw',    # NULL
        'batt_soc',           # NULL
        'irradiance_wm2',     # มี
        'ambient_temp_c',     # มี
        'panel_temp_c',       # มี
        'grid_voltage_avg_v', # NULL
        'grid_frequency_hz',  # NULL
        'fuel_level_pct',     # NULL
        'plim_gtp_1',         # มี
        'irrt_kwh_m2',        # มี
    ]
    for col in ALL_COLS:
        if col not in df.columns:
            df[col] = None

    insert_sql = """
        INSERT INTO metering_history
            (timestamp,
             pv_power_kw, load_power_kw,
             batt_power_kw, grid_import_kw, grid_export_kw,
             genset_power_kw, batt_soc,
             irradiance_wm2, ambient_temp_c, panel_temp_c,
             grid_voltage_avg_v, grid_frequency_hz, fuel_level_pct,
             plim_gtp_1, irrt_kwh_m2)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            pv_power_kw    = VALUES(pv_power_kw),
            load_power_kw  = VALUES(load_power_kw),
            irradiance_wm2 = VALUES(irradiance_wm2),
            ambient_temp_c = VALUES(ambient_temp_c),
            panel_temp_c   = VALUES(panel_temp_c),
            plim_gtp_1     = VALUES(plim_gtp_1),
            irrt_kwh_m2    = VALUES(irrt_kwh_m2)
    """

    if dry_run:
        out = os.path.join(output_dir, 'bcp2026_metering_preview.csv')
        df[ALL_COLS].to_csv(out, index=False)
        logger.info(f'  [DRY RUN] Would insert {len(df):,} rows')
        logger.info(f'  [DRY RUN] Preview: {out}')
        return True

    try:
        conn   = mysql.connector.connect(**EDGE_DB_CONFIG)
        cursor = conn.cursor()
        logger.info(f'  Edge DB connection: SUCCESS')
    except mysql.connector.Error as e:
        logger.error(f'  Edge DB FAILED: {e}')
        return False

    total    = len(df)
    upserted = 0
    batch_size = 500
    buf = []

    def to_dec(val, digits=2):
        return round(float(val), digits) if pd.notna(val) else None

    for _, row in df[ALL_COLS].iterrows():
        buf.append((
            row['timestamp'],
            to_dec(row['pv_power_kw']),
            to_dec(row['load_power_kw']),
            None,                                                     # batt_power_kw
            None,                                                     # grid_import_kw
            None,                                                     # grid_export_kw
            None,                                                     # genset_power_kw
            None,                                                     # batt_soc
            int(row['irradiance_wm2']) if pd.notna(row['irradiance_wm2']) else None,
            to_dec(row['ambient_temp_c']),
            to_dec(row['panel_temp_c']),
            None,                                                     # grid_voltage_avg_v
            None,                                                     # grid_frequency_hz
            None,                                                     # fuel_level_pct
            to_dec(row['plim_gtp_1']),
            to_dec(row['irrt_kwh_m2'], 4) if pd.notna(row.get('irrt_kwh_m2', np.nan)) else None,
        ))

        if len(buf) >= batch_size:
            cursor.executemany(insert_sql, buf)
            conn.commit()
            upserted += len(buf)
            buf = []
            logger.info(f'  Progress: {upserted:,}/{total:,} ({upserted/total*100:.0f}%)')

    if buf:
        cursor.executemany(insert_sql, buf)
        conn.commit()
        upserted += len(buf)

    cursor.close()
    conn.close()
    logger.info(f'  metering_history upserted: {upserted:,} rows — DONE')
    return True


# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description='GridMind — BCP 2026 Importer -> solar_edge.metering_history (1-min)')
    parser.add_argument('--data-dir',   type=str,            help='Folder ที่มีไฟล์ bcp_*.csv (default: .)')
    parser.add_argument('--output-dir', type=str,            help='Folder สำหรับ backup/log (default: ./gridmind_output)')
    parser.add_argument('--noct',       type=int,            help='NOCT value (default: 45)')
    parser.add_argument('--dry-run',    action='store_true', help='ทดสอบโดยไม่ insert DB')
    args = parser.parse_args()

    data_dir   = args.data_dir   or '.'
    output_dir = args.output_dir or './gridmind_output'
    noct       = args.noct       or int(os.getenv('NOCT', '45'))
    dry_run    = args.dry_run

    os.makedirs(output_dir, exist_ok=True)

    logger.info('=' * 60)
    logger.info('GridMind AI V2.2 — BCP 2026 Importer -> metering_history (1-min)')
    logger.info('=' * 60)
    logger.info(f'Data dir : {data_dir}')
    logger.info(f'NOCT     : {noct}')
    logger.info(f'DB       : {EDGE_DB_CONFIG["host"]}/{EDGE_DB_CONFIG["database"]}')
    logger.info(f'Table    : metering_history')
    if dry_run:
        logger.info('Mode     : DRY RUN (no DB write)')

    # Pipeline
    df_main  = load_main_files(data_dir)         # STEP 1: bcp_Ld_Gtp_temp
    df_pv    = load_pvtemp_files(data_dir)       # STEP 2: bcp_PVtemp_Irr
    df_merge = parse_and_merge(df_main, df_pv)   # STEP 3: merge + UTC index
    df_clean = clean_data(df_merge)              # STEP 3b: clean
    df_1m    = resample_1min(df_clean, noct)     # STEP 4: resample 1 นาที
    save_to_metering_history(df_1m, dry_run, output_dir)  # STEP 5: insert DB

    # CSV backup
    out = os.path.join(output_dir, 'bcp2026_metering_output.csv')
    export = df_1m.copy().reset_index()
    export['timestamp'] = export['timestamp'].dt.tz_convert('Asia/Bangkok').dt.tz_localize(None)
    export.to_csv(out, index=False)

    logger.info('\n' + '=' * 60)
    logger.info('IMPORT COMPLETE')
    logger.info('=' * 60)
    logger.info(f'  Processed : {len(df_1m):,} rows (1-min intervals)')
    logger.info(f'  Range     : {df_1m.index.min()} to {df_1m.index.max()}')
    logger.info(f'  CSV backup: {out}')
    logger.info(f'  Log saved : import_bcp2026_metering.log')


if __name__ == '__main__':
    main()
