import pandas as pd
import os

# =========================================================
# CONFIG: ปรับชื่อไฟล์ตรงนี้เท่านั้น
# =========================================================
CONFIGS = [
    {
        "main_file":  "202511.txt",
        "pv_file":    "pv_temp_202511.csv",
        "output":     "merged_202511.csv",
        # 202511.txt ใช้ M/D/YYYY  → dayfirst=False
        # pv_temp    ใช้ D/M/YYYY  → dayfirst=True
        "main_dayfirst": False,
        "pv_dayfirst":   True,
    },
]

# column mapping: ชื่อใน main_file → ชื่อ output สุดท้าย
RENAME_MAP = {
    "amb temp":       "Amb temp",     # 202511.txt lowercase
    "Irradiance_Wm2": "Irr_W_m2",    # 202511.txt → standard name
    "Irrt_Wh_m2":     "Irrt_kWh_m2", # 202511.txt (unit ต่างแต่ map ตามที่ต้องการ)
}

FINAL_COLS = [
    "DatetimeServer",
    "GTP1_Total_Power_kW",
    "Aload_SumkW",
    "plim_gtp_1",
    "Amb temp",
    "PV temp",
    "Irr_W_m2",
    "Irrt_kWh_m2",
]

FILL_ZERO_COLS = [
    "GTP1_Total_Power_kW",
    "Aload_SumkW",
    "plim_gtp_1",
    "Amb temp",
    "Irr_W_m2",
    "Irrt_kWh_m2",
]


def load_file(path, dayfirst=True):
    ext = os.path.splitext(path)[-1].lower()
    if ext == ".csv":
        df = pd.read_csv(path)
    elif ext == ".txt":
        df = pd.read_csv(path, sep="\t")
    else:
        raise ValueError(f"Unsupported file type: {ext}")

    df.columns = df.columns.str.strip()
    df["DatetimeServer"] = pd.to_datetime(
        df["DatetimeServer"], dayfirst=dayfirst, errors="coerce"
    )
    df = df.dropna(subset=["DatetimeServer"])
    df = df.sort_values("DatetimeServer").reset_index(drop=True)
    return df


def merge_month(cfg):
    print(f"\n{'='*50}")
    print(f"Processing: {cfg['main_file']} + {cfg['pv_file']}")

    df_main = load_file(cfg["main_file"], dayfirst=cfg["main_dayfirst"])
    df_pv   = load_file(cfg["pv_file"],   dayfirst=cfg["pv_dayfirst"])

    print(f"  main rows : {len(df_main):,}  ({df_main['DatetimeServer'].min()} → {df_main['DatetimeServer'].max()})")
    print(f"  pv   rows : {len(df_pv):,}  ({df_pv['DatetimeServer'].min()} → {df_pv['DatetimeServer'].max()})")

    # rename ให้ตรง standard ก่อน merge
    df_main = df_main.rename(columns=RENAME_MAP)

    # merge โดยใช้ pv เป็นหลัก (left = pv)
    df = pd.merge(df_pv, df_main, on="DatetimeServer", how="left")

    # เติม 0 ให้คอลัมน์ที่ไม่มีข้อมูล
    for col in FILL_ZERO_COLS:
        if col in df.columns:
            df[col] = df[col].fillna(0)

    # จัด column ตามลำดับที่ต้องการ (เอาเฉพาะที่มีจริง)
    out_cols = [c for c in FINAL_COLS if c in df.columns]
    df = df[out_cols]

    df.to_csv(cfg["output"], index=False)
    print(f"  ✅ output  : {cfg['output']}  ({len(df):,} rows)")
    return df


# =========================================================
# MAIN
# =========================================================
for cfg in CONFIGS:
    merge_month(cfg)

print("\n✅ Done! ทุกไฟล์ merge สำเร็จแล้ว")