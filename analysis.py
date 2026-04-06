"""
阶段 6：读取 wenshu_cases.csv，输出统计表与图表到 data/plots/。

说明：无 GeoJSON 时不做真热力图；标的分段以「诉请标的额」为主（无法解析为数字的记为缺失）。
字体：优先 PingFang SC / Arial Unicode MS / SimHei / Noto Sans CJK SC。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_mpl_cfg = Path(__file__).resolve().parent / ".mplconfig"
_mpl_cfg.mkdir(exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_mpl_cfg))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _setup_matplotlib_chinese() -> None:
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["font.sans-serif"] = [
        "PingFang SC",
        "Arial Unicode MS",
        "Heiti TC",
        "SimHei",
        "Noto Sans CJK SC",
        "Microsoft YaHei",
        "DejaVu Sans",
    ]


def _coerce_money(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.replace("", np.nan).astype(str).str.replace(",", "", regex=False), errors="coerce")


def _win_score(s: str) -> float | np.floating:
    if pd.isna(s) or str(s).strip() == "":
        return np.nan
    t = str(s).strip()
    if t == "原告胜诉":
        return 1.0
    if t == "部分胜诉":
        return 0.5
    if t == "败诉":
        return 0.0
    return np.nan


def run_analysis(csv_path: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    _setup_matplotlib_chinese()

    df = pd.read_csv(csv_path, encoding="utf-8-sig", dtype=str)
    df = df.replace({np.nan: "", "nan": ""})

    for c in ("案由", "所在省份", "是否胜诉"):
        if c not in df.columns:
            df[c] = ""

    df["_案由"] = df["案由"].replace("", "(空)").fillna("(空)")
    df["_省份"] = df["所在省份"].replace("", "(空)").fillna("(空)")
    df["_诉请"] = _coerce_money(df["诉请标的额"] if "诉请标的额" in df.columns else pd.Series([np.nan] * len(df)))
    df["_支持率"] = pd.to_numeric(
        df["标的支持率"].replace("", np.nan) if "标的支持率" in df.columns else np.nan,
        errors="coerce",
    )
    df["_胜诉分"] = df["是否胜诉"].map(_win_score)

    # --- 案由 Top10 ---
    ay = df["_案由"].value_counts().head(10)
    ay.to_csv(out_dir / "案由_top10.csv", encoding="utf-8-sig", header=["件数"])
    fig, ax = plt.subplots(figsize=(8, max(3, len(ay) * 0.35)))
    ay.sort_values().plot(kind="barh", ax=ax, color="#2c5aa0")
    ax.set_title("案由 Top10")
    ax.set_xlabel("件数")
    plt.tight_layout()
    fig.savefig(out_dir / "案由_top10.png", dpi=150)
    plt.close()

    # --- 省份 ---
    prov = df["_省份"].value_counts()
    prov.to_csv(out_dir / "省份_count.csv", encoding="utf-8-sig", header=["件数"])
    fig, ax = plt.subplots(figsize=(8, max(3, len(prov) * 0.4)))
    prov.sort_values().plot(kind="barh", ax=ax, color="#c44e52")
    ax.set_title("省份分布（按条数）")
    ax.set_xlabel("件数")
    plt.tight_layout()
    fig.savefig(out_dir / "省份_bar.png", dpi=150)
    plt.close()

    # --- 诉请标的额分段 ---
    bins = [0, 1e4, 5e4, 10e4, 50e4, np.inf]
    labels = ["0–1万", "1–5万", "5–10万", "10–50万", "50万+"]
    valid = df["_诉请"].dropna()
    if len(valid) > 0:
        cats = pd.cut(valid, bins=bins, labels=labels, right=False)
        seg = cats.value_counts().reindex(labels, fill_value=0)
    else:
        seg = pd.Series({l: 0 for l in labels})
    seg.to_csv(out_dir / "诉请标的额分段.csv", encoding="utf-8-sig", header=["件数"])
    fig, ax = plt.subplots(figsize=(8, 4))
    seg.plot(kind="bar", ax=ax, color="#4c72b0", rot=25)
    ax.set_title("诉请标的额分段（件数）")
    ax.set_ylabel("件数")
    plt.tight_layout()
    fig.savefig(out_dir / "诉请标的额分段_bar.png", dpi=150)
    plt.close()

    # --- 是否胜诉 饼图 ---
    vc = df["是否胜诉"].replace("", "(空)").value_counts()
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.pie(vc.values, labels=vc.index, autopct="%1.1f%%", startangle=90)
    ax.set_title("是否胜诉占比")
    plt.tight_layout()
    fig.savefig(out_dir / "胜诉占比_pie.png", dpi=150)
    plt.close()

    # --- 案由 / 省份 × 胜诉率（仅统计有明确胜诉标签的）---
    def pivot_rate(gcol: str, name: str) -> None:
        sub = df[df["_胜诉分"].notna()].copy()
        if sub.empty:
            pd.DataFrame().to_csv(out_dir / name, encoding="utf-8-sig")
            return
        t = sub.groupby(gcol, dropna=False).agg(
            条数=("案号", "count"),
            胜诉率均值=("_胜诉分", "mean"),
        )
        t["胜诉率均值"] = t["胜诉率均值"].round(4)
        t.to_csv(out_dir / name, encoding="utf-8-sig")

    pivot_rate("_案由", "胜诉率_按案由.csv")
    pivot_rate("_省份", "胜诉率_按省份.csv")

    # --- 标的支持率 ---
    sr = df["_支持率"].dropna()
    stats = pd.DataFrame(
        {
            "指标": ["有效条数", "均值", "中位数", "最小", "最大"],
            "值": [
                len(sr),
                float(sr.mean()) if len(sr) else np.nan,
                float(sr.median()) if len(sr) else np.nan,
                float(sr.min()) if len(sr) else np.nan,
                float(sr.max()) if len(sr) else np.nan,
            ],
        }
    )
    stats.to_csv(out_dir / "标的支持率汇总.csv", index=False, encoding="utf-8-sig")
    if len(sr) >= 2:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.hist(sr.clip(0, 2), bins=min(15, len(sr)), color="#55a868", edgecolor="white")
        ax.set_title("标的支持率分布（0–2 截断显示）")
        ax.set_xlabel("标的支持率")
        ax.set_ylabel("件数")
        plt.tight_layout()
        fig.savefig(out_dir / "标的支持率_hist.png", dpi=150)
        plt.close()

    print(f"图表与表已写入: {out_dir.resolve()}")


def main() -> int:
    default_csv = os.environ.get("ANALYSIS_INPUT_CSV", "data/wenshu_cases.csv")
    default_out = os.environ.get("ANALYSIS_PLOTS_DIR", "data/plots")

    ap = argparse.ArgumentParser(
        description="裁判文书案例表可视化",
        epilog="示例: python analysis.py        # 默认读 data/wenshu_cases.csv\n"
        "      python analysis.py data/wenshu_cases.csv -o data/plots",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "csv",
        nargs="?",
        default=default_csv,
        metavar="CSV",
        help="案例表路径（默认: data/wenshu_cases.csv；勿使用文档占位符 path/to/...）",
    )
    ap.add_argument(
        "-o",
        "--out",
        default=default_out,
        metavar="DIR",
        help="图表输出目录（默认: data/plots）",
    )
    args = ap.parse_args()

    p = Path(args.csv)
    if not p.is_file():
        print(f"找不到 CSV: {p.resolve()}，请先运行 export_cases.py", file=sys.stderr)
        return 1

    run_analysis(p, Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
