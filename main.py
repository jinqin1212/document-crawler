"""
裁判文书网「腾讯电子签」流水线入口。列表检索 + 多页翻页（MAX_PAGES）。
"""
from __future__ import annotations

import asyncio
import os
import sys

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import wenshu_spider


def _env_bool(key: str, default: str = "false") -> bool:
    return os.environ.get(key, default).strip().lower() in ("1", "true", "yes", "on")


def _env_int(key: str, default: int = 0) -> int:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def main() -> int:
    os.environ.get("WENSHU_USER", "")
    os.environ.get("WENSHU_PASS", "")
    output_dir = os.environ.get("OUTPUT_DIR", "./data")
    headless = _env_bool("HEADLESS", "false")
    max_pages = _env_int("MAX_PAGES", 1)
    if max_pages < 1:
        max_pages = 1
    storage_state = os.environ.get("STORAGE_STATE_PATH", "").strip() or None

    keyword = os.environ.get("SEARCH_KEYWORD", wenshu_spider.DEFAULT_KEYWORD)
    date_end = os.environ.get("SEARCH_DATE_END", wenshu_spider.DEFAULT_DATE_END)
    date_start = os.environ.get("SEARCH_DATE_START", wenshu_spider.DEFAULT_DATE_START)
    login_pause = _env_bool("WENSHU_LOGIN_PAUSE", "false")
    click_nav_login = _env_bool("WENSHU_CLICK_NAV_LOGIN", "true")
    simple_only = _env_bool("WENSHU_SIMPLE_SEARCH_ONLY", "false")

    asyncio.run(
        wenshu_spider.run_phase1_list(
            headless=headless,
            storage_state_path=storage_state,
            output_dir=output_dir,
            keyword=keyword,
            date_end=date_end,
            date_start=date_start,
            login_pause=login_pause,
            click_nav_login=click_nav_login,
            simple_search_only=simple_only,
            max_pages=max_pages,
        )
    )

    print(
        f"Done. OUTPUT_DIR={output_dir!r}, MAX_PAGES={max_pages}, HEADLESS={headless}, "
        f"STORAGE_STATE_PATH={storage_state!r}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
