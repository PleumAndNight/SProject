#!/usr/bin/env python3
"""
GridMind AI V2.2 — Raw File Importer → metering_history
=========================================================
อ่านไฟล์ .txt / .csv เหมือน Block 1 ของ run_block123
แต่แทนที่จะ resample เป็น 15 นาที → resample เป็น 1 นาที
แล้ว insert ลงตาราง metering_history (Edge DB: solar_edge)

Pipeline:
    ไฟล์ 20*.txt / 20*.csv + pv_temp_*.csv
        → Load → Clean → Resample 1 นาที → metering_history

Requirements:
    pip install pandas numpy mysql-connector-python

Usage:
    python3 import_metering.py                        # อ่านทุกไฟล์ใน folder ปัจจุบัน
    python3 import_metering.py --data-dir /path/data  # ระบุ folder
    python3 import_metering.py --dry-run              # ทดสอบโดยไม่ insert DB
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
    'password': '',             # <-- ใส่รหัสผ่าน
}

# ============================================================
# Logging
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(open(sys.stdout.fileno(), mode='w', encoding='utf-8', closefd=False)),
        logging.FileHandler('import_metering.log', mode='w', encoding='utf-8'),
    ]
)
logger = logging.getLogger('gridmind')


# ============================================================
# Config
# ============================================================
def get_config(args):
    return {
        'NOCT':       args.noct     or int(os.getenv('NOCT', '45')),
        'DATA_DIR':   args.data_dir or '.',
        'OUTPUT_DIR': args.output_dir or './gridmind_output',
        'DRY_RUN':    args.dry_run,
        # Column names ในไฟล์ต้นฉบับ
        'COL_TIMESTAMP':    'DatetimeServer',
        'COL_PV_POWER':     'GTP1_Total_Power_kW',
        'COL_PLIM':         'plim_gtp_1',
        'COL_LOAD':         'Aload_SumkW',
        'COL_AMBIENT_TEMP': 'amb temp',
        'COL_IRRADIANCE':   'Irradiance_Wm2',
        'COL_PV_TEMP':      'PV temp',
    }


# ============================================================
# STEP 1+2: Load ไฟล์ + Parse timestamp (เหมือน block1 ทุกอย่าง)
# ============================================================
def load_files(cfg):
    C = cfg
    txt_files = sorted([f for f in glob.glob(os.path.join(C['DATA_DIR'], '20*.txt'))
                        if 'pv_temp' not in os.path.basename(f).lower()])
    csv_files = sorted([f for f in glob.glob(os.path.join(C['DATA_DIR'], '20*.csv'))
                        if 'pv_temp' not in os.path.basename(f).lower()])
    all_files = txt_files + csv_files

    if not all_files:
        logger.error(f'No data files found in {C["DATA_DIR"]}')
        sys.exit(1)

    logger.info(f'\n[1/5] Loading {len(all_files)} data file(s):')
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

            # Normalize column names
            col_map = {}
            for col in chunk.columns:
                cl = col.strip().lower()
                if cl == 'amb temp':
                    col_map[col] = 'amb temp'
                elif cl in ('irr_w_m2', 'irradiance_wm2'):
                    col_map[col] = 'Irradiance_Wm2'
            if col_map:
                chunk.rename(columns=col_map, inplace=True)

            # Auto-detect PLIM scale (>500 = หน่วยต่างกัน)
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
            logger.warning(f'  Failed {f}: {e}')

    if not dfs:
        logger.error('No data loaded')
        sys.exit(1)

    df = pd.concat(dfs, ignore_index=True)
    logger.info(f'Total raw: {len(df):,} rows')

    # Load pv_temp files
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
                logger.warning(f'    Failed {f}: {e}')
        if pv_dfs:
            df_pv_temp = pd.concat(pv_dfs, ignore_index=True)
            df_pv_temp['timestamp'] = pd.to_datetime(
                df_pv_temp['DatetimeServer'], format='mixed', dayfirst=True, errors='coerce')
            df_pv_temp = df_pv_temp.dropna(subset=['timestamp'])
            logger.info(f'  PV temp total: {len(df_pv_temp):,} rows | '
                        f'{df_pv_temp["timestamp"].min()} to {df_pv_temp["timestamp"].max()}')

    # Parse timestamps (.txt = M/D/Y, .csv = D/M/Y เหมือน block1)
    logger.info('\n[2/5] Parsing timestamps...')
    is_csv_mask = df['_is_csv'] == True
    if (~is_csv_mask).any():
        df.loc[~is_csv_mask, 'timestamp'] = pd.to_datetime(
            df.loc[~is_csv_mask, C['COL_TIMESTAMP']], format='mixed', dayfirst=False, errors='coerce')
        logger.info(f'  .txt (M/D/Y): {df.loc[~is_csv_mask, "timestamp"].notna().sum():,}')
    if is_csv_mask.any():
        df.loc[is_csv_mask, 'timestamp'] = pd.to_datetime(
            df.loc[is_csv_mask, C['COL_TIMESTAMP']], format='mixed', dayfirst=True, errors='coerce')
        logger.info(f'  .csv (D/M/Y): {df.loc[is_csv_mask, "timestamp"].notna().sum():,}')

    df = df.drop(columns=['_is_csv'])
    before = len(df)
    df = df.dropna(subset=['timestamp'])
    logger.info(f'  Total: {len(df):,} / {before:,} (lost {before - len(df)})')

    # Merge PV temp
    if df_pv_temp is not None and len(df_pv_temp) > 0:
        logger.info('  Merging PV temp files...')
        df = df.sort_values('timestamp')
        df_pv_temp = df_pv_temp.sort_values('timestamp')

        merge_cols = ['timestamp']
        if 'PV temp' in df_pv_temp.columns:
            if 'PV temp' in df.columns:
                df = df.drop(columns=['PV temp'])
            merge_cols.append('PV temp')

        has_irr_pv   = 'Irradiance_Wm2' in df_pv_temp.columns
        had_irr_main = 'Irradiance_Wm2' in df.columns
        if has_irr_pv:
            if had_irr_main:
                df.rename(columns={'Irradiance_Wm2': '_irr_main'}, inplace=True)
            merge_cols.append('Irradiance_Wm2')

        df = pd.merge_asof(df, df_pv_temp[merge_cols].drop_duplicates('timestamp'),
                           on='timestamp', direction='nearest', tolerance=pd.Timedelta('2min'))
        matched = df[merge_cols[1]].notna().sum()
        logger.info(f'  Merged {merge_cols[1:]}: {matched:,}/{len(df):,} ({matched/len(df)*100:.1f}%)')

        if has_irr_pv and had_irr_main and '_irr_main' in df.columns:
            df['Irradiance_Wm2'] = df['_irr_main'].fillna(df['Irradiance_Wm2'])
            df = df.drop(columns=['_irr_main'])

    df = df.set_index('timestamp')
    df.index = df.index.tz_localize('Asia/Bangkok').tz_convert('UTC')
    return df


# ============================================================
# STEP 3: Clean 
# ============================================================
def clean_data(df, cfg):
    C = cfg
    logger.info('\n[3/5] Cleaning data...')

    # -0.999 = sensor error
    df = df.replace(-0.999, np.nan)

    # อุณหภูมิ 0 = sensor error (ไทยไม่มี 0 degC)
    for col in [C['COL_AMBIENT_TEMP'], C['COL_PV_TEMP']]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
            n = (df[col] == 0).sum()
            if n > 0:
                df.loc[df[col] == 0, col] = np.nan
                logger.info(f'  {col}: replaced {n:,} zeros with NaN')

    # Physical range filter
    TEMP_RANGES = {
        C['COL_AMBIENT_TEMP']: (15.0, 55.0),
        C['COL_PV_TEMP']:      (15.0, 85.0),
    }
    for col, (lo, hi) in TEMP_RANGES.items():
        if col in df.columns:
            bad = ((df[col] < lo) | (df[col] > hi)) & df[col].notna()
            n = bad.sum()
            if n > 0:
                df.loc[bad, col] = np.nan
                logger.info(f'  {col}: replaced {n:,} out-of-range values (valid: {lo}-{hi} degC)')

    # Drop rows ที่ critical columns เป็น NaN
    critical = [C['COL_PV_POWER'], C['COL_PLIM'], C['COL_LOAD']]
    before = len(df)
    df = df.dropna(subset=critical)
    dropped = before - len(df)
    logger.info(f'  Critical drop: {before:,} -> {len(df):,} (dropped {dropped:,})')

    # Interpolate non-critical (สูงสุด 4 จุด)
    for col in [C['COL_AMBIENT_TEMP'], C['COL_IRRADIANCE'], C['COL_PV_TEMP']]:
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
# STEP 4: Resample เป็น 1 นาที + map columns → metering_history schema
# ============================================================
def resample_1min(df, cfg):
    C = cfg
    logger.info('\n[4/5] Resampling to 1-min intervals...')

    # Panel temp: sensor ถ้ามี, ไม่งั้น NOCT
    nf = (C['NOCT'] - 20) / 800.0
    has_pv_temp = (C['COL_PV_TEMP'] in df.columns and
                   df[C['COL_PV_TEMP']].notna().sum() > len(df) * 0.3)
    if has_pv_temp:
        noct_calc = df[C['COL_AMBIENT_TEMP']] + nf * df[C['COL_IRRADIANCE']].fillna(0)
        df['_panel_temp'] = df[C['COL_PV_TEMP']].fillna(noct_calc)
        valid = df[C['COL_PV_TEMP']].notna().sum()
        logger.info(f'  Panel temp: PV SENSOR {valid:,} rows + NOCT fallback')
    else:
        df['_panel_temp'] = df[C['COL_AMBIENT_TEMP']] + nf * df[C['COL_IRRADIANCE']].fillna(0)
        logger.info(f'  Panel temp: NOCT formula')

    # column ที่จะ resample
    resample_map = {
        C['COL_PV_POWER']:     'pv_power_kw',
        C['COL_LOAD']:         'load_power_kw',
        C['COL_IRRADIANCE']:   'irradiance_wm2',
        C['COL_AMBIENT_TEMP']: 'ambient_temp_c',
        '_panel_temp':         'panel_temp_c',
        C['COL_PLIM']:         'plim_gtp_1',
    }
    # เอาเฉพาะ column ที่มีอยู่จริง
    resample_map = {k: v for k, v in resample_map.items() if k in df.columns}

    df_1m = df[list(resample_map.keys())].resample('1min').mean()
    df_1m = df_1m.rename(columns=resample_map)
    df_1m = df_1m.dropna(subset=['pv_power_kw', 'load_power_kw'])

    logger.info(f'  Resampled: {len(df_1m):,} intervals')
    logger.info(f'  Range: {df_1m.index.min()} to {df_1m.index.max()}')
    return df_1m


# ============================================================
# STEP 5: Insert ลง metering_history (Edge DB)
# ============================================================
def save_to_metering_history(df_1m, cfg):
    logger.info('\n[5/5] Save to Edge DB (metering_history)...')

    # แปลง UTC index → Bangkok naive datetime
    df = df_1m.copy().reset_index()
    df['timestamp'] = (
        df['timestamp']
        .dt.tz_convert('Asia/Bangkok')
        .dt.tz_localize(None)
    )

    DB_COLS = [
        'timestamp', 'pv_power_kw', 'load_power_kw',
        'irradiance_wm2', 'ambient_temp_c', 'panel_temp_c', 'plim_gtp_1',
    ]
    for col in DB_COLS:
        if col not in df.columns:
            df[col] = None

    insert_sql = """
        INSERT INTO metering_history
            (timestamp, pv_power_kw, load_power_kw,
             irradiance_wm2, ambient_temp_c, panel_temp_c, plim_gtp_1)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            pv_power_kw    = VALUES(pv_power_kw),
            load_power_kw  = VALUES(load_power_kw),
            irradiance_wm2 = VALUES(irradiance_wm2),
            ambient_temp_c = VALUES(ambient_temp_c),
            panel_temp_c   = VALUES(panel_temp_c),
            plim_gtp_1     = VALUES(plim_gtp_1)
    """

    # Dry run — save CSV แทน insert
    if cfg['DRY_RUN']:
        out = os.path.join(cfg['OUTPUT_DIR'], 'metering_history_import_preview.csv')
        df[DB_COLS].to_csv(out, index=False)
        logger.info(f'  [DRY RUN] Would insert {len(df):,} rows')
        logger.info(f'  [DRY RUN] Preview saved: {out}')
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

    for _, row in df[DB_COLS].iterrows():
        buf.append((
            row['timestamp'],
            round(float(row['pv_power_kw']),    2) if pd.notna(row['pv_power_kw'])    else None,
            round(float(row['load_power_kw']),  2) if pd.notna(row['load_power_kw'])  else None,
            int(row['irradiance_wm2'])             if pd.notna(row['irradiance_wm2']) else None,
            round(float(row['ambient_temp_c']), 2) if pd.notna(row['ambient_temp_c']) else None,
            round(float(row['panel_temp_c']),   2) if pd.notna(row['panel_temp_c'])   else None,
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
    logger.info(f'  metering_history upserted: {upserted:,} rows — DONE')
    return True


# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser(description='GridMind — Raw File Importer -> metering_history')
    parser.add_argument('--data-dir',   type=str,            help='Folder ที่มีไฟล์ 20*.txt / 20*.csv (default: .)')
    parser.add_argument('--output-dir', type=str,            help='Folder สำหรับ backup/log (default: ./gridmind_output)')
    parser.add_argument('--noct',       type=int,            help='NOCT value (default: 45)')
    parser.add_argument('--dry-run',    action='store_true', help='ทดสอบโดยไม่ insert DB — save CSV แทน')
    args = parser.parse_args()

    cfg = get_config(args)
    os.makedirs(cfg['OUTPUT_DIR'], exist_ok=True)

    logger.info('=' * 60)
    logger.info('GridMind AI V2.2 — Raw File Importer -> metering_history')
    logger.info('=' * 60)
    logger.info(f'Data dir : {cfg["DATA_DIR"]}')
    logger.info(f'NOCT     : {cfg["NOCT"]}')
    logger.info(f'Edge DB  : {EDGE_DB_CONFIG["host"]}/{EDGE_DB_CONFIG["database"]}')
    if cfg['DRY_RUN']:
        logger.info('Mode     : DRY RUN (no DB write)')

    df_raw   = load_files(cfg)               # STEP 1+2: Load + Parse timestamp
    df_clean = clean_data(df_raw, cfg)       # STEP 3:   Clean
    df_1m    = resample_1min(df_clean, cfg)  # STEP 4:   Resample 1 นาที
    save_to_metering_history(df_1m, cfg)     # STEP 5:   Insert DB

    logger.info('\n' + '=' * 60)
    logger.info('IMPORT COMPLETE')
    logger.info('=' * 60)
    logger.info(f'  Total inserted : {len(df_1m):,} rows (1-min intervals)')
    logger.info(f'  Date range     : {df_1m.index.min()} to {df_1m.index.max()}')
    logger.info(f'  Log saved      : import_metering.log')


if __name__ == '__main__':
    main()
