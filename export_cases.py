"""
阶段 5：将 parser 产出的预览表清洗为正式 wenshu_cases.csv / wenshu_cases.xlsx。

前置：已运行 python parser.py 生成 data/wenshu_parsed_preview.csv

用法：
  python export_cases.py
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pandas as pd

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from parser import FIELD_KEYS


def main() -> int:
    inp = Path(os.environ.get("EXPORT_INPUT_CSV", "data/wenshu_parsed_preview.csv"))
    out_csv = Path(os.environ.get("EXPORT_OUT_CSV", "data/wenshu_cases.csv"))
    out_xlsx = Path(os.environ.get("EXPORT_OUT_XLSX", "data/wenshu_cases.xlsx"))

    if not inp.is_file():
        print(f"找不到输入表: {inp.resolve()}，请先运行 python parser.py")
        return 1

    df = pd.read_csv(inp, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    # 读入后空单元格可能是 "" 或字面 "nan"
    df = df.replace({"nan": "", "NaN": "", "None": ""})

    for k in FIELD_KEYS:
        if k not in df.columns:
            df[k] = ""

    meta: list[str] = []
    for c in ("source_file", "文书标题"):
        if c in df.columns:
            meta.append(c)

    keep_snippet = os.environ.get("EXPORT_KEEP_RAW_SNIPPET", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    if keep_snippet and "raw_标的片段" in df.columns:
        meta.append("raw_标的片段")

    df["导出时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    ordered = list(FIELD_KEYS) + meta + ["导出时间"]
    df = df[[c for c in ordered if c in df.columns]]

    for c in df.columns:
        df[c] = df[c].astype(str).str.strip()
        df[c] = df[c].replace({"nan": "", "NaT": ""})

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    df.to_excel(out_xlsx, index=False, engine="openpyxl")

    print(
        f"已导出 {len(df)} 行 → {out_csv.resolve()}\n"
        f"              → {out_xlsx.resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
