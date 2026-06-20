#!/usr/bin/env python3
"""
GridMind AI V2.2 — BCP 2026 Importer → solar_edge.3m_2026
===========================================================
อ่านไฟล์ bcp_Ld_Gtp_temp_YYYYMM.csv + bcp_PVtemp_Irr_YYYYMM.csv
→ Clean → Resample 15 นาที → บันทึกลง solar_edge.3m_2026 (Edge DB)

Column mapping (2026 format → 3m_2026 schema):
    bcp_Ld_Gtp_temp:   GTP1_Total_Power_kW → pv_power_kw
                       Aload_SumkW         → load_power_kw
                       plim_gtp_1 / 100    → plim_gtp_1
                       an5                 → ambient_temp_c
    bcp_PVtemp_Irr:    an3                 → panel_temp_c
                       Irr_W_m2            → irradiance_wm2
                       Irrt_kWh_m2         → irrt_kwh_m2

Requirements:
    pip install pandas numpy mysql-connector-python

Usage:
    python3 import_bcp2026.py                        # อ่านทุกไฟล์ใน folder ปัจจุบัน
    python3 import_bcp2026.py --data-dir /path/data  # ระบุ folder
    python3 import_bcp2026.py --dry-run              # ทดสอบโดยไม่ insert DB
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
# DB Config  (Edge DB: solar_edge)
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
        logging.FileHandler('import_bcp2026.log', mode='w', encoding='utf-8'),
    ]
)
logger = logging.getLogger('gridmind')


# ============================================================
# SQL: สร้างตาราง 3m_2026 ใน solar_edge (รันครั้งแรกอัตโนมัติ)
# ============================================================
CREATE_AVG_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS `3m_2026` (
  `timestamp`      datetime      NOT NULL,
  `pv_power_kw`    decimal(10,2) DEFAULT NULL,
  `load_power_kw`  decimal(10,2) DEFAULT NULL,
  `irradiance_wm2` int(11)       DEFAULT NULL,
  `ambient_temp_c` decimal(5,2)  DEFAULT NULL,
  `panel_temp_c`   decimal(5,2)  DEFAULT NULL,
  `sample_count`   int(11)       DEFAULT NULL,
  `irrt_kwh_m2`    decimal(10,4) DEFAULT NULL COMMENT 'Irradiation total kWh/m2',
  `plim_gtp_1`     decimal(10,2) DEFAULT NULL COMMENT 'Power limit GTP1 kW',
  PRIMARY KEY (`timestamp`),
  KEY `idx_timestamp` (`timestamp`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
PARTITION BY RANGE (to_days(`timestamp`))
(
  PARTITION p_2026_q1 VALUES LESS THAN (740072),
  PARTITION p_2026_q2 VALUES LESS THAN (740163),
  PARTITION p_2026_q3 VALUES LESS THAN (740255),
  PARTITION p_2026_q4 VALUES LESS THAN (740347),
  PARTITION p_future  VALUES LESS THAN MAXVALUE
);
"""


def ensure_table():
    try:
        conn   = mysql.connector.connect(**EDGE_DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute(CREATE_AVG_TABLE_SQL)
        conn.commit()
        cursor.close()
        conn.close()
        logger.info('  3m_2026 table: ready')
    except mysql.connector.Error as e:
        logger.error(f'  Cannot create 3m_2026: {e}')
        sys.exit(1)


# ============================================================
# STEP 1: Load ไฟล์ bcp_Ld_Gtp_temp_*.csv
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
# STEP 2: Load ไฟล์ bcp_PVtemp_Irr_*.csv
# ============================================================
def load_pvtemp_files(data_dir):
    files = sorted(glob.glob(os.path.join(data_dir, 'bcp_PVtemp_Irr_*.csv')))
    if not files:
        logger.warning('  No bcp_PVtemp_Irr_*.csv files found — panel_temp/irradiance will use fallback')
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

    # Parse timestamp (ISO format Y-M-D)
    df_pv['timestamp'] = pd.to_datetime(df_pv['DatetimeServer'], errors='coerce')
    df_pv = df_pv.dropna(subset=['timestamp'])

    # Rename columns → ชื่อกลาง
    df_pv = df_pv.rename(columns={
        'an3':          'panel_temp_c',
        'Irr_W_m2':     'irradiance_wm2',
        'Irrt_kWh_m2':  'irrt_kwh_m2',
    })

    logger.info(f'  PVtemp/Irr total: {len(df_pv):,} rows | '
                f'{df_pv["timestamp"].min()} to {df_pv["timestamp"].max()}')
    return df_pv


# ============================================================
# STEP 3: Parse timestamp + Merge + Set index
# ============================================================
def parse_and_merge(df_main, df_pv):
    logger.info('\n[3/5] Parse timestamp & merge...')

    # Timestamp ไฟล์ใหม่เป็น ISO (Y-M-D H:M:S) — parse ตรงได้เลย
    df_main['timestamp'] = pd.to_datetime(df_main['DatetimeServer'], errors='coerce')
    before = len(df_main)
    df_main = df_main.dropna(subset=['timestamp'])
    logger.info(f'  Main: {len(df_main):,} / {before:,} (lost {before - len(df_main)})')

    # Rename main columns → ชื่อกลาง
    df_main = df_main.rename(columns={
        'GTP1_Total_Power_kW': 'pv_power_kw',
        'Aload_SumkW':         'load_power_kw',
        'an5':                 'ambient_temp_c',
    })

    # Merge PVtemp/Irr ด้วย merge_asof (tolerance 2 นาที เหมือน block1)
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
# STEP 3b: Clean (เหมือน block1)
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
    logger.info(f'  Critical drop: {before:,} -> {len(df):,} (dropped {before-len(df):,})')

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
# STEP 4: Resample เป็น 15 นาที
# ============================================================
def resample_15min(df, noct=45):
    logger.info('\n[4/5] Resampling to 15-min intervals...')

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

    AGG = {
        'pv_power_kw':    'mean',
        'load_power_kw':  'mean',
        'irradiance_wm2': 'mean',
        'ambient_temp_c': 'mean',
        'panel_temp_c':   'mean',
        'irrt_kwh_m2':    'mean',
        'plim_gtp_1':     'mean',
    }
    AGG = {k: v for k, v in AGG.items() if k in df.columns}

    cnt = df['pv_power_kw'].resample('15min').count().rename('sample_count')
    df_15m = df[list(AGG.keys())].resample('15min').agg(AGG)
    df_15m = df_15m.join(cnt)
    df_15m = df_15m.dropna(subset=['pv_power_kw', 'load_power_kw'])

    logger.info(f'  Resampled: {len(df_15m):,} intervals')
    logger.info(f'  Range: {df_15m.index.min()} to {df_15m.index.max()}')
    return df_15m


# ============================================================
# STEP 5: Insert ลง solar_edge.3m_2026
# ============================================================
def save_to_db(df_15m, dry_run=False, output_dir='./gridmind_output'):
    logger.info('\n[5/5] Save to DB (solar_edge.3m_2026)...')

    df = df_15m.copy().reset_index()
    df['timestamp'] = (
        df['timestamp']
        .dt.tz_convert('Asia/Bangkok')
        .dt.tz_localize(None)
    )

    DB_COLS = [
        'timestamp', 'pv_power_kw', 'load_power_kw',
        'irradiance_wm2', 'ambient_temp_c', 'panel_temp_c',
        'sample_count', 'irrt_kwh_m2', 'plim_gtp_1',
    ]
    for col in DB_COLS:
        if col not in df.columns:
            df[col] = None

    insert_sql = """
        INSERT INTO `3m_2026`
            (timestamp, pv_power_kw, load_power_kw,
             irradiance_wm2, ambient_temp_c, panel_temp_c,
             sample_count, irrt_kwh_m2, plim_gtp_1)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            pv_power_kw    = VALUES(pv_power_kw),
            load_power_kw  = VALUES(load_power_kw),
            irradiance_wm2 = VALUES(irradiance_wm2),
            ambient_temp_c = VALUES(ambient_temp_c),
            panel_temp_c   = VALUES(panel_temp_c),
            sample_count   = VALUES(sample_count),
            irrt_kwh_m2    = VALUES(irrt_kwh_m2),
            plim_gtp_1     = VALUES(plim_gtp_1)
    """

    if dry_run:
        out = os.path.join(output_dir, 'bcp2026_avg15min_preview.csv')
        df[DB_COLS].to_csv(out, index=False)
        logger.info(f'  [DRY RUN] Would insert {len(df):,} rows')
        logger.info(f'  [DRY RUN] Preview: {out}')
        return True

    try:
        conn   = mysql.connector.connect(**EDGE_DB_CONFIG)
        cursor = conn.cursor()
        logger.info(f'  DB connection: SUCCESS')
    except mysql.connector.Error as e:
        logger.error(f'  DB FAILED: {e}')
        return False

    total    = len(df)
    upserted = 0
    batch_size = 500
    buf = []

    for _, row in df[DB_COLS].iterrows():
        buf.append((
            row['timestamp'],
            round(float(row['pv_power_kw']),    2) if pd.notna(row['pv_power_kw'])    else None,
            round(float(row['load_power_kw']),  2) if pd.notna(row['load_power_kw'])  else None,
            int(row['irradiance_wm2'])             if pd.notna(row['irradiance_wm2']) else None,
            round(float(row['ambient_temp_c']), 2) if pd.notna(row['ambient_temp_c']) else None,
            round(float(row['panel_temp_c']),   2) if pd.notna(row['panel_temp_c'])   else None,
            int(row['sample_count'])               if pd.notna(row['sample_count'])   else None,
            round(float(row['irrt_kwh_m2']),    4) if pd.notna(row['irrt_kwh_m2'])    else None,
            round(float(row['plim_gtp_1']),     2) if pd.notna(row['plim_gtp_1'])     else None,
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
    logger.info(f'  3m_2026 upserted: {upserted:,} rows — DONE')
    return True


# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser(description='GridMind — BCP 2026 Importer -> solar_edge.3m_2026')
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
    logger.info('GridMind AI V2.2 — BCP 2026 Importer -> solar_edge.3m_2026')
    logger.info('=' * 60)
    logger.info(f'Data dir : {data_dir}')
    logger.info(f'NOCT     : {noct}')
    logger.info(f'DB       : {EDGE_DB_CONFIG["host"]}/{EDGE_DB_CONFIG["database"]}')
    if dry_run:
        logger.info('Mode     : DRY RUN (no DB write)')

    # สร้างตารางถ้ายังไม่มี
    if not dry_run:
        ensure_table()

    # Pipeline
    df_main  = load_main_files(data_dir)           # STEP 1: bcp_Ld_Gtp_temp
    df_pv    = load_pvtemp_files(data_dir)         # STEP 2: bcp_PVtemp_Irr
    df_merge = parse_and_merge(df_main, df_pv)     # STEP 3: merge + UTC index
    df_clean = clean_data(df_merge)                # STEP 3b: clean
    df_15m   = resample_15min(df_clean, noct)      # STEP 4: resample 15 นาที
    save_to_db(df_15m, dry_run, output_dir)        # STEP 5: insert DB

    # CSV backup
    out = os.path.join(output_dir, 'bcp2026_avg15min_output.csv')
    export = df_15m.copy().reset_index()
    export['timestamp'] = export['timestamp'].dt.tz_convert('Asia/Bangkok').dt.tz_localize(None)
    export.to_csv(out, index=False)

    logger.info('\n' + '=' * 60)
    logger.info('IMPORT COMPLETE')
    logger.info('=' * 60)
    logger.info(f'  Processed : {len(df_15m):,} intervals (15-min)')
    logger.info(f'  Range     : {df_15m.index.min()} to {df_15m.index.max()}')
    logger.info(f'  CSV backup: {out}')
    logger.info(f'  Log saved : import_bcp2026.log')


if __name__ == '__main__':
    main()