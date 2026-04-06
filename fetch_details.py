"""
阶段 3：读取列表 JSON，逐条打开详情页，保存 HTML 到 data/raw/。

用法：
  cd 项目根 && source .venv/bin/activate
  python fetch_details.py

环境变量（可选，见 config.example.env）：
  DETAIL_LIST_JSON, DETAIL_OUTPUT_DIR, DETAIL_URL_BASE, DETAIL_MAX_ITEMS,
  DETAIL_RETRIES, DETAIL_SKIP_EXISTING, STORAGE_STATE_PATH, HEADLESS
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

from playwright.async_api import async_playwright

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from wenshu_selectors import DETAIL_WAIT_SELECTORS
from wenshu_spider import _load_cookies_if_present, wait_out_of_waf

logger = logging.getLogger(__name__)

_ILLEGAL_FS = re.compile(r'[\\/:*?"<>|\n\r\t]')


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )


def _resolve_absolute_url(href: str, base: str) -> str:
    href = (href or "").strip()
    if href.startswith("http"):
        return href
    return urljoin(base, href)


def _output_basename(row: dict[str, str], url: str, index: int) -> str:
    case = (row.get("案号") or "").strip()
    if case:
        safe = _ILLEGAL_FS.sub("_", case)[:180]
        return f"{safe}.html"
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    doc_id = (qs.get("docId") or [""])[0]
    if doc_id:
        h = hashlib.sha256(f"{index}:{doc_id}".encode("utf-8")).hexdigest()[:24]
        return f"doc_{h}.html"
    h2 = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return f"item_{index:04d}_{h2}.html"


async def _wait_detail_content(page) -> None:
    """
    等待正文容器。旧逻辑曾对「每个」选择器各等 25s，与页面不符时会白白等几分钟；
    改为逗号组合一次等待 + 短稳定延时（你肉眼已加载完时足够落盘）。
    """
    timeout_ms = int(os.environ.get("DETAIL_BODY_TIMEOUT_MS", "8000"))
    stabilize = float(os.environ.get("DETAIL_STABILIZE_S", "0.5") or "0.5")
    combined = ", ".join(DETAIL_WAIT_SELECTORS)
    try:
        await page.locator(combined).first.wait_for(
            state="attached",
            timeout=timeout_ms,
        )
    except Exception:
        logger.debug("组合选择器在 %dms 内未命中，仍继续（页面可能用新结构）", timeout_ms)
    await asyncio.sleep(stabilize)


async def _fetch_one(
    page,
    url: str,
    out_path: Path,
    *,
    retries: int,
) -> bool:
    for attempt in range(1, retries + 1):
        try:
            # domcontentloaded 通常比 load 早触发，不必等所有图片/统计请求
            await page.goto(url, wait_until="domcontentloaded", timeout=120_000)
            await wait_out_of_waf(page)
            await _wait_detail_content(page)
            await asyncio.sleep(random.uniform(0.15, 0.45))
            html = await page.content()
            if len(html) < 500:
                raise RuntimeError(f"页面过短 ({len(html)} bytes)，可能未加载正文")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(html, encoding="utf-8")
            logger.info("已保存 %s", out_path.name)
            return True
        except Exception as e:
            logger.warning("第 %d/%d 次失败 %s: %s", attempt, retries, url[:80], e)
            if attempt < retries:
                await asyncio.sleep(2.0 * attempt)
    return False


async def run_fetch_details() -> int:
    _setup_logging()
    list_path = Path(os.environ.get("DETAIL_LIST_JSON", "data/wenshu_list_preview.json"))
    out_dir = Path(os.environ.get("DETAIL_OUTPUT_DIR", "data/raw"))
    url_base = os.environ.get(
        "DETAIL_URL_BASE",
        "https://wenshu.court.gov.cn/website/wenshu/181029CR4M5A62CH/index.html",
    )
    storage = os.environ.get("STORAGE_STATE_PATH", "").strip() or None
    headless = os.environ.get("HEADLESS", "false").lower() in ("1", "true", "yes")
    retries = int(os.environ.get("DETAIL_RETRIES", "3") or "3")
    retries = max(1, retries)
    max_items = int(os.environ.get("DETAIL_MAX_ITEMS", "0") or "0")
    skip_existing = os.environ.get("DETAIL_SKIP_EXISTING", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    delay_lo = float(os.environ.get("DETAIL_DELAY_MIN", "1.5") or "1.5")
    delay_hi = float(os.environ.get("DETAIL_DELAY_MAX", "4.0") or "4.0")

    if not list_path.is_file():
        logger.error("找不到列表文件: %s", list_path.resolve())
        return 1

    rows: list[dict] = json.loads(list_path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        logger.error("列表 JSON 应为数组")
        return 1

    if max_items > 0:
        rows = rows[:max_items]

    failed_path = Path(os.environ.get("DETAIL_FAILED_LOG", "data/failed_cases.txt"))
    failed_path.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        ctx_kw: dict = {}
        if storage and Path(storage).is_file():
            ctx_kw["storage_state"] = storage
        context = await browser.new_context(**ctx_kw)
        await _load_cookies_if_present(context, Path("data/cookies.json"))
        page = await context.new_page()
        page.set_default_timeout(120_000)

        ok, bad = 0, 0
        for i, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                continue
            href = (row.get("链接") or "").strip()
            if not href:
                logger.warning("第 %d 条无链接，跳过", i)
                bad += 1
                continue
            url = _resolve_absolute_url(href, url_base)
            name = _output_basename(row, url, i)
            target = out_dir / name

            if skip_existing and target.is_file() and target.stat().st_size > 500:
                logger.info("已存在，跳过: %s", name)
                ok += 1
                continue

            logger.info("[%d/%d] 抓取 %s", i, len(rows), url[:100])
            if await _fetch_one(page, url, target, retries=retries):
                ok += 1
            else:
                bad += 1
                with failed_path.open("a", encoding="utf-8") as ff:
                    ff.write(f"{url}\t{row.get('列表标题', '')}\n")

            if i < len(rows):
                await asyncio.sleep(random.uniform(delay_lo, delay_hi))

        await browser.close()

    print(f"详情抓取结束: 成功 {ok}, 失败 {bad}, 输出目录 {out_dir.resolve()}")
    return 1 if bad > 0 else 0


def main() -> int:
    return asyncio.run(run_fetch_details())


if __name__ == "__main__":
    raise SystemExit(main())
