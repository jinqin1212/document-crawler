"""
阶段 4：裁判文书详情 HTML → 13 字段结构化字典。

规则说明（无法 100% 准确，以空字符串/「无法判断」为主，不伪造）：
- 标的额：在「诉讼请求」等片段内用正则抓「数字+元」；多个时取首个合理值；判项里「支持…元」另抓为判决标的。
- 标的支持率：仅当诉请标的、判决标的均为正数时计算 judgment/claim，保留两位小数。
- 是否胜诉：关键词映射（全部支持/驳回诉讼请求等）；否定语境仅做简易处理。
- 缺席、公告送达、代理、腾讯电子签效力：关键词表 + 简易否定（「未」「无」「不」紧邻时降置信，输出仍用是/否/无法判断）。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

# 输出列顺序（与计划一致）
FIELD_KEYS: tuple[str, ...] = (
    "案号",
    "案由",
    "诉请标的额",
    "判决标的额",
    "标的支持率",
    "是否胜诉",
    "受理法院",
    "所在省份",
    "审理天数",
    "被告是否缺席",
    "是否明确论述腾讯电子签效力",
    "原告有无代理",
    "法院有无公告送达",
)

_PROVINCES: tuple[str, ...] = (
    "北京",
    "天津",
    "河北",
    "山西",
    "内蒙古",
    "辽宁",
    "吉林",
    "黑龙江",
    "上海",
    "江苏",
    "浙江",
    "安徽",
    "福建",
    "江西",
    "山东",
    "河南",
    "湖北",
    "湖南",
    "广东",
    "广西",
    "海南",
    "重庆",
    "四川",
    "贵州",
    "云南",
    "西藏",
    "陕西",
    "甘肃",
    "青海",
    "宁夏",
    "新疆",
)

_CASE_NO_RE = re.compile(r"[（(]\d{4}[）)][^。\n]{0,40}?号")
_MONEY_RE = re.compile(r"([\d０-９，,]+\.?[\d０-９]*)\s*元")


def _norm_num(s: str) -> float | None:
    s = (
        s.replace(",", "")
        .replace("，", "")
        .replace(" ", "")
        .translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    )
    try:
        v = float(s)
        return v if v >= 0 else None
    except ValueError:
        return None


def _first_money_in(text: str) -> float | None:
    for m in _MONEY_RE.finditer(text):
        v = _norm_num(m.group(1))
        if v is not None and v > 0:
            return v
    return None


def _segment_after(text: str, keyword: str, maxlen: int = 800) -> str:
    i = text.find(keyword)
    if i < 0:
        return ""
    return text[i : i + maxlen]


def _extract_claim_amount(full: str) -> float | None:
    for kw in ("诉讼请求", "诉请", "要求被告", "请求判令", "请求法院"):
        seg = _segment_after(full, kw, 1200)
        if seg:
            v = _first_money_in(seg)
            if v is not None:
                return v
    return None


def _extract_judgment_amount(full: str) -> float | None:
    for kw in ("判决如下", "判令", "支持原告", "支持.*诉讼请求", "驳回原告"):
        seg = _segment_after(full, kw, 1200)
        if seg:
            v = _first_money_in(seg)
            if v is not None:
                return v
    # 判项里常见的「元」
    seg = _segment_after(full, "给付", 600)
    if seg:
        v = _first_money_in(seg)
        if v is not None:
            return v
    return None


def _support_rate(claim: float | None, judgment: float | None) -> str:
    if claim is None or judgment is None:
        return ""
    if claim <= 0:
        return ""
    return f"{judgment / claim:.2f}"


def _win_status(full: str) -> str:
    if "驳回原告" in full and "全部诉讼请求" in full:
        return "败诉"
    if "驳回诉讼请求" in full and "全部" in full[max(0, full.find("驳回诉讼请求") - 20) : full.find("驳回诉讼请求") + 40]:
        return "败诉"
    if re.search(r"全部支持|予以全部支持|全额支持", full):
        return "原告胜诉"
    if re.search(r"部分支持|部分予以支持|部分诉请", full):
        return "部分胜诉"
    if "驳回" in full and "诉讼请求" in full:
        return "败诉"
    return "无法判断"


def _province_from_court(court: str) -> str:
    for p in _PROVINCES:
        if p in court:
            return p
    return ""


def _trial_days(full: str) -> str:
    m1 = re.search(r"立案日期[：:]\s*(\d{4}-\d{2}-\d{2})", full)
    m2 = re.search(r"(?:裁判日期|判决日期)[：:]\s*(\d{4}-\d{2}-\d{2})", full)
    if m1 and m2:
        try:
            d1 = datetime.strptime(m1.group(1), "%Y-%m-%d")
            d2 = datetime.strptime(m2.group(1), "%Y-%m-%d")
            return str((d2 - d1).days)
        except ValueError:
            pass
    return ""


def _defendant_absent(full: str) -> str:
    if re.search(r"被告[^。;；]{0,50}(缺席|未到庭)", full):
        return "是"
    if "缺席判决" in full:
        return "是"
    if re.search(r"被告[^。;；]{0,50}到庭", full) or "未缺席" in full:
        return "否"
    return "无法判断"


def _tx_e_sign_effect(full: str) -> str:
    if "腾讯电子签" not in full:
        return "否"
    idx = full.find("腾讯电子签")
    seg = full[max(0, idx - 30) : idx + 80]
    if re.search(r"(效力|有效|合法|真实性|认[可定])", seg):
        if re.search(r"(无效|不予认定|不认可|没有效力)", seg):
            return "否"
        return "是"
    return "否"


def _plaintiff_agent(full: str) -> str:
    if re.search(r"原告.*委托.*律师|原告.*律师", full):
        return "律师"
    if re.search(r"公民代理", full) and "原告" in full:
        return "公民代理"
    if re.search(r"原告.*未到庭|无诉讼代理人", full):
        return "无"
    return "无法判断"


def _announcement(full: str) -> str:
    return "是" if "公告送达" in full else "否"


def _soup_main_text(soup: BeautifulSoup) -> str:
    box = soup.select_one(".PDF_box") or soup.select_one("#content") or soup.body
    if not box:
        return soup.get_text("\n", strip=True)
    return box.get_text("\n", strip=True)


def _soup_meta(soup: BeautifulSoup) -> tuple[str, str, str, str]:
    """案号、案由、受理法院、标题"""
    case_no = ""
    ah = soup.select_one("#ahdiv")
    if ah:
        case_no = ah.get_text(strip=True)
    if not case_no:
        m = _CASE_NO_RE.search(_soup_main_text(soup))
        if m:
            case_no = m.group(0).strip()

    cause = ""
    ay = soup.select_one("#aydiv")
    if ay:
        cause = ay.get_text(strip=True)
    if not cause:
        td = soup.find("td", string=re.compile(r"案\s*由"))
        if td and td.find_next_sibling("td"):
            cause = td.find_next_sibling("td").get_text(strip=True)

    court = ""
    for h4 in soup.find_all("h4", class_=re.compile(r"clearfix")):
        sp = h4.find("span")
        if not sp:
            continue
        if "审理法院" in sp.get_text():
            a = h4.find("a")
            court = a.get_text(strip=True) if a else h4.get_text(" ", strip=True)
            break

    title_el = soup.select_one(".PDF_title")
    title = title_el.get_text(strip=True) if title_el else ""

    return case_no, cause, court, title


def parse_html(html: str, *, source_path: str | None = None) -> dict[str, Any]:
    """
    解析单份 HTML 字符串，返回含 13 字段的字典；
    另含 source_file、文书标题、raw_标的片段（可选调试，短片段）。
    """
    soup = BeautifulSoup(html, "html.parser")
    full = _soup_main_text(soup)
    case_no, cause, court, title = _soup_meta(soup)

    claim = _extract_claim_amount(full)
    judgment = _extract_judgment_amount(full)
    rate = _support_rate(claim, judgment)

    def _fmt_money(x: float | None) -> str:
        if x is None or x <= 0:
            return ""
        return str(int(x)) if abs(x - round(x)) < 1e-9 else str(x)

    row: dict[str, Any] = {k: "" for k in FIELD_KEYS}
    row["案号"] = case_no
    row["案由"] = cause or ""
    row["诉请标的额"] = _fmt_money(claim)
    row["判决标的额"] = _fmt_money(judgment)
    row["标的支持率"] = rate
    row["是否胜诉"] = _win_status(full)
    row["受理法院"] = court
    row["所在省份"] = _province_from_court(court)
    row["审理天数"] = _trial_days(full)
    row["被告是否缺席"] = _defendant_absent(full)
    row["是否明确论述腾讯电子签效力"] = _tx_e_sign_effect(full)
    row["原告有无代理"] = _plaintiff_agent(full)
    row["法院有无公告送达"] = _announcement(full)

    seg_claim = _segment_after(full, "诉讼请求", 400)
    row["source_file"] = source_path or ""
    row["文书标题"] = title
    row["raw_标的片段"] = (seg_claim[:200] + "…") if len(seg_claim) > 200 else seg_claim

    return row


def parse_html_file(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    out = parse_html(text, source_path=str(p.name))
    return out


def parse_raw_dir(
    raw_dir: str | Path,
    *,
    pattern: str = "*.html",
) -> list[dict[str, Any]]:
    d = Path(raw_dir)
    rows: list[dict[str, Any]] = []
    for f in sorted(d.glob(pattern)):
        if not f.is_file():
            continue
        try:
            rows.append(parse_html_file(f))
        except Exception as e:
            rows.append(
                {
                    **{k: "" for k in FIELD_KEYS},
                    "source_file": f.name,
                    "文书标题": "",
                    "raw_标的片段": "",
                    "parse_error": str(e),
                }
            )
    return rows


def main() -> int:
    import os

    import pandas as pd

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    raw_dir = Path(os.environ.get("PARSER_RAW_DIR", "data/raw"))
    out_csv = Path(os.environ.get("PARSER_OUT_CSV", "data/wenshu_parsed_preview.csv"))
    out_json = Path(os.environ.get("PARSER_OUT_JSON", "data/wenshu_parsed_preview.json"))

    rows = parse_raw_dir(raw_dir)
    if not rows:
        print(f"未找到 HTML: {raw_dir}")
        return 1

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    cols = list(FIELD_KEYS) + [
        c
        for c in ("source_file", "文书标题", "raw_标的片段", "parse_error")
        if c in df.columns
    ]
    df = df[[c for c in cols if c in df.columns]]
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    out_json.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"已解析 {len(rows)} 个文件 → {out_csv.resolve()} 与 {out_json.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
