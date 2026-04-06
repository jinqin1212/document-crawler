"""
中国裁判文书网爬虫 — 阶段 1：高级检索 + 结果列表第一页。

仅限合法授权研究使用；请遵守站点条款、限速与礼貌爬取。遇强反爬时请采用文档中的降级路径，
禁止伪造字段。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Frame, Page, async_playwright

from wenshu_selectors import ADVANCED_SEARCH_URL, SELECTORS

logger = logging.getLogger(__name__)

DEFAULT_KEYWORD = "腾讯电子签"
DEFAULT_CASE_TYPE = "民事案件"
DEFAULT_DOC_TYPE = "判决书"
DEFAULT_DATE_END = "2026-03-28"
DEFAULT_DATE_START = "2000-01-01"

_CASE_NO_RE = re.compile(r"[（(]\d{4}[）)].*?号")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _setup_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )


def _parse_row_fields(
    cells: list[str],
    link_text: str,
    href: str | None,
) -> dict[str, str]:
    merged = " ".join(cells) + " " + link_text
    case_no = ""
    m = _CASE_NO_RE.search(merged)
    if m:
        case_no = m.group(0).strip()
    court = ""
    for c in cells:
        c = c.strip()
        if "法院" in c and len(c) < 120:
            court = c
            break
    judge_date = ""
    for c in cells:
        dm = _DATE_RE.search(c)
        if dm:
            judge_date = dm.group(0)
            break
    if not case_no:
        for c in cells:
            c = c.strip()
            if c and "号" in c and re.search(r"\d", c):
                case_no = c
                break
    rel = href or ""
    if rel.startswith("/"):
        rel = f"https://wenshu.court.gov.cn{rel}"
    return {
        "案号": case_no,
        "受理法院": court,
        "裁判日期": judge_date,
        "列表标题": link_text.strip(),
        "链接": rel,
    }


SearchRoot = Page | Frame


async def wait_out_of_waf(page: Page, *, timeout_ms: int = 300_000) -> None:
    """若落在文书网 WAF 验证页，等待人工完成直至 URL 离开 waf_text_verify。"""
    if "waf_text_verify" not in page.url:
        return
    logger.warning(
        "当前为 WAF 验证页，请在浏览器中完成验证；完成后脚本会自动继续（最长等待 %.0f 分钟）",
        timeout_ms / 60_000,
    )
    await page.wait_for_function(
        "() => !window.location.href.includes('waf_text_verify')",
        timeout=timeout_ms,
    )
    logger.info("已离开 WAF 验证页")
    await asyncio.sleep(0.8)


async def maybe_click_nav_login(page: Page, *, want_click: bool) -> bool:
    """
    want_click 为 True 时尝试点击顶栏「登录」(open=login)。
    注意：已登录时仍可能触发 WAF / 页面跳转；脚本会在随后 wait_out_of_waf 并必要时重回高级检索 URL。
    """
    if not want_click:
        logger.info("已配置不自动点击顶栏「登录」（WENSHU_CLICK_NAV_LOGIN=false）")
        return False
    return await click_nav_login_if_present(page)


def _advanced_click_targets(p: Page | Frame):
    """尽量覆盖文书网各种「高级检索」入口（含 tab/span）。"""
    return (
        p.get_by_role("link", name="高级检索"),
        p.get_by_role("button", name="高级检索"),
        p.get_by_role("tab", name="高级检索"),
        p.locator("a:has-text('高级检索')").first,
        p.locator("span:has-text('高级检索')").first,
        p.locator("li:has-text('高级检索')").first,
        p.locator(SELECTORS["advanced_search_entry"]).first,
        p.locator("text=高级查询").first,
        p.locator("[onclick*='高级检索']").first,
    )


async def _try_click_advanced_anywhere(page: Page) -> bool:
    for host in (page, *page.frames):
        for loc in _advanced_click_targets(host):
            try:
                if await loc.count() == 0:
                    continue
                el = loc.first
                if not await el.is_visible():
                    continue
                await el.click(timeout=4_000)
                logger.info("已点击「高级检索」类入口（%s）", type(host).__name__)
                await asyncio.sleep(1.5)
                return True
            except Exception:
                continue
    return False


async def _has_keyvalue1(target: SearchRoot) -> bool:
    return await target.locator(SELECTORS["keyword_input"]).count() > 0


async def _prepare_key_input_root(target: SearchRoot) -> bool:
    """#keyValue1 在 DOM 中即可（常被 iframe 延迟加载或先 hidden）。"""
    loc = target.locator(SELECTORS["keyword_input"]).first
    try:
        await loc.wait_for(state="attached", timeout=3_000)
    except Exception:
        return False
    try:
        await loc.scroll_into_view_if_needed(timeout=5_000)
    except Exception:
        pass
    return True


def _log_all_frames(page: Page) -> None:
    logger.info("当前主 URL: %s", page.url[:200] if page.url else "")
    for i, fr in enumerate(page.frames):
        u = fr.url or ""
        logger.info("  frame[%d] name=%r url=%s", i, fr.name, u[:160] if u else u)


async def resolve_search_root(page: Page, *, poll_seconds: int = 50) -> SearchRoot:
    """
    定位含 #keyValue1 的高级检索表单。
    文书网常把表单放在延迟加载的 iframe 内，需轮询；必要时自动点「高级检索」类入口。
    """
    logger.info("正在定位高级检索表单 (#keyValue1)，最多等待约 %d 秒…", poll_seconds)
    await page.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(1.5)

    # 先尝试点一次高级入口（忽略失败），再等 iframe
    await _try_click_advanced_anywhere(page)

    deadline = time.monotonic() + poll_seconds
    while time.monotonic() < deadline:
        contexts: list[SearchRoot] = [page]
        for fr in page.frames:
            u = fr.url or ""
            if not u or u.startswith("about:blank"):
                continue
            contexts.append(fr)

        for ctx in contexts:
            if not await _has_keyvalue1(ctx):
                continue
            if await _prepare_key_input_root(ctx):
                tag = "主页面" if isinstance(ctx, Page) else f"iframe {((ctx.url or '')[:90])}"
                logger.info("已找到 #keyValue1（%s）", tag)
                return ctx

        await asyncio.sleep(1.0)

    # 最后一轮：再点一次高级入口并短等
    if await _try_click_advanced_anywhere(page):
        await asyncio.sleep(2.0)
        for ctx in [page, *page.frames]:
            if await _has_keyvalue1(ctx) and await _prepare_key_input_root(ctx):
                logger.info("点击高级入口后找到 #keyValue1")
                return ctx

    _log_all_frames(page)
    raise RuntimeError(
        "仍找不到 #keyValue1。请在本机浏览器打开当前 URL，F12 搜索 keyValue1："
        "若在 iframe 里，把该 iframe 的 src 或截图发给维护者；"
        "或临时设置环境变量 WENSHU_SIMPLE_SEARCH_ONLY=true 仅用首页关键词搜索（无民事/判决书筛选）。"
    )


async def apply_simple_home_search(page: Page, *, keyword: str) -> None:
    """首页大搜索框：填关键词后点右侧 div.search-rightBtn.search-click「搜索」。"""
    logger.warning(
        "使用首页普通搜索；不会设置民事/判决书/日期（需高级检索请关 WENSHU_SIMPLE_SEARCH_ONLY）"
    )
    inp = page.locator(SELECTORS["home_keyword_input"]).first
    await inp.wait_for(state="visible", timeout=30_000)
    await inp.fill(keyword)
    go = page.locator(SELECTORS["home_search_click"]).first
    try:
        await go.wait_for(state="visible", timeout=10_000)
        await go.click(timeout=15_000)
    except Exception:
        await page.get_by_role("button", name="搜索").first.click(timeout=15_000)


async def apply_filters(
    root: SearchRoot,
    *,
    keyword: str = DEFAULT_KEYWORD,
    case_type: str = DEFAULT_CASE_TYPE,
    doc_type: str = DEFAULT_DOC_TYPE,
    date_end: str = DEFAULT_DATE_END,
    date_start: str = DEFAULT_DATE_START,
) -> None:
    """高级检索：关键词、民事、判决书、裁判日期区间。"""
    logger.info("正在填写检索条件并点击检索…")
    kw = root.locator(SELECTORS["keyword_input"]).first
    try:
        await kw.fill(keyword, timeout=30_000)
    except Exception:
        await kw.fill(keyword, force=True, timeout=30_000)
    await root.click(SELECTORS["case_type_trigger"])
    await root.locator(f"text={case_type}").first.click()
    await root.click(SELECTORS["doc_type_trigger"])
    await root.locator(f"text={doc_type}").first.click()
    await root.fill(SELECTORS["date_start"], date_start)
    await root.fill(SELECTORS["date_end"], date_end)
    await root.click(SELECTORS["search_btn"])


async def click_nav_login_if_present(
    page: Page, *, timeout_ms: int = 8_000
) -> bool:
    """
    若存在「登录」链接触发 open=login，则点击一次以刷新会话展示。
    页面若有多处相同 onclick，使用 .first（可在 F12 用 querySelectorAll 核对数量）。
    """
    loc = page.locator(SELECTORS["nav_login"]).first
    try:
        await loc.wait_for(state="visible", timeout=timeout_ms)
        await loc.click()
        await asyncio.sleep(0.6)
        logger.info("已点击顶栏「登录」链接（open=login）")
        return True
    except Exception:
        logger.info("未找到可见的 open=login 登录链，跳过（可能已在检索 iframe 内，需改 frame）")
        return False


async def wait_results_anywhere(
    page: Page, *, timeout_ms: int = 120_000
) -> SearchRoot:
    """结果列表可能在主页面或任意 iframe，依次等待。"""
    ctxs: list[SearchRoot] = [page]
    for fr in page.frames:
        u = fr.url or ""
        if u.startswith("about:blank") and not fr.name:
            continue
        ctxs.append(fr)
    n = len(ctxs)
    per = max(15_000, timeout_ms // max(n, 1))
    last: Exception | None = None
    for c in ctxs:
        try:
            await c.locator(SELECTORS["result_case_link"]).first.wait_for(
                state="visible",
                timeout=per,
            )
            logger.info(
                "结果列表已出现（%s）",
                "主页面" if isinstance(c, Page) else f"iframe: {(c.url or '')[:80]}",
            )
            return c
        except Exception as e:
            last = e
            continue
    raise TimeoutError("未在任何页面/frame 等到结果列表 a.caseName") from last


async def parse_first_page_list(root: SearchRoot) -> list[dict[str, str]]:
    """解析当前页列表：案号、受理法院、裁判日期及列表可见字段。"""
    loc = root.locator(SELECTORS["result_case_link"])
    n = await loc.count()
    rows: list[dict[str, str]] = []
    for i in range(n):
        link = loc.nth(i)
        href = await link.get_attribute("href")
        title = (await link.text_content() or "").strip()
        row = link.locator(SELECTORS["result_row_xpath"])
        cells: list[str] = []
        if await row.count() > 0:
            td = row.locator("td")
            if await td.count() > 0:
                cells = [t.strip() for t in await td.all_inner_texts()]
        rows.append(_parse_row_fields(cells, title, href))
    return rows


def _row_dedupe_key(row: dict[str, str]) -> str:
    k = (row.get("案号") or "").strip()
    if k:
        return k
    return (row.get("链接") or row.get("列表标题") or "").strip() or repr(row)


async def click_next_page_anywhere(list_root: SearchRoot, page: Page) -> bool:
    """在列表所在 frame 或主页面/其他 frame 中点击「下一页」。"""
    order: list[SearchRoot] = []
    seen: set[int] = set()

    def _push(x: SearchRoot) -> None:
        i = id(x)
        if i not in seen:
            seen.add(i)
            order.append(x)

    _push(list_root)
    _push(page)
    for fr in page.frames:
        _push(fr)

    for ctx in order:
        groups = (
            ctx.get_by_role("link", name="下一页"),
            ctx.locator(SELECTORS["pagination_next"]),
        )
        for group in groups:
            try:
                if await group.count() == 0:
                    continue
                el = group.first
                if not await el.is_visible():
                    continue
                cls = (await el.get_attribute("class")) or ""
                if "disabled" in cls.lower() or "gray" in cls.lower():
                    continue
                parent = el.locator("xpath=ancestor::li[1]")
                if await parent.count() > 0:
                    pcl = (await parent.first.get_attribute("class")) or ""
                    if "disabled" in pcl.lower():
                        continue
                await el.click(timeout=12_000)
                logger.info("已点击「下一页」")
                return True
            except Exception:
                continue
    return False


async def crawl_result_list_pages(
    page: Page,
    list_root: SearchRoot,
    *,
    max_pages: int,
    delay_min_s: float = 1.5,
    delay_max_s: float = 4.0,
) -> tuple[list[dict[str, str]], SearchRoot]:
    """
    阶段 2：多页列表，案号去重，页间随机延迟。
    返回 (合并后的行, 最后一次解析时使用的 list_root)。
    """
    max_pages = max(1, max_pages)
    seen: set[str] = set()
    merged: list[dict[str, str]] = []
    root = list_root

    for page_idx in range(1, max_pages + 1):
        rows = await parse_first_page_list(root)
        logger.info("第 %d 页解析 %d 条", page_idx, len(rows))
        for row in rows:
            key = _row_dedupe_key(row)
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(row)

        if page_idx >= max_pages:
            break

        await asyncio.sleep(random.uniform(delay_min_s, delay_max_s))
        if not await click_next_page_anywhere(root, page):
            logger.info("无「下一页」或已到末页，停止在 %d 页", page_idx)
            break
        root = await wait_results_anywhere(page)

    return merged, root


async def _load_cookies_if_present(context: BrowserContext, path: Path) -> None:
    if not path.is_file():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            await context.add_cookies(raw)
            logger.info("已加载 cookies: %s", path)
    except Exception as e:
        logger.warning("加载 cookies 失败（可忽略）: %s", e)


class WenshuSpider:
    """阶段 1：打开高级检索、筛选、取第一页列表。"""

    def __init__(
        self,
        *,
        headless: bool = False,
        storage_state_path: str | None = None,
        cookies_json: Path | None = None,
        slow_mo_ms: int = 0,
    ) -> None:
        self.headless = headless
        self.storage_state_path = storage_state_path
        self.cookies_json = cookies_json or Path("data/cookies.json")
        self.slow_mo_ms = slow_mo_ms
        self._pw = None
        self._browser = None
        self._context = None
        self.page: Page | None = None

    async def __aenter__(self) -> WenshuSpider:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo_ms,
        )
        ctx_kw: dict[str, Any] = {}
        if self.storage_state_path and Path(self.storage_state_path).is_file():
            ctx_kw["storage_state"] = self.storage_state_path
        self._context = await self._browser.new_context(**ctx_kw)
        await _load_cookies_if_present(self._context, self.cookies_json)
        self.page = await self._context.new_page()
        self.page.set_default_timeout(120_000)
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def open_advanced_search(self) -> None:
        assert self.page is not None
        await self.page.goto(ADVANCED_SEARCH_URL, wait_until="load")
        await wait_out_of_waf(self.page)
        await asyncio.sleep(1.0)

    async def apply_filters(self, root: SearchRoot, **kwargs: Any) -> None:
        await apply_filters(root, **kwargs)


async def run_phase1_list(
    *,
    headless: bool = False,
    storage_state_path: str | None = None,
    output_dir: str = "./data",
    keyword: str = DEFAULT_KEYWORD,
    case_type: str = DEFAULT_CASE_TYPE,
    doc_type: str = DEFAULT_DOC_TYPE,
    date_end: str = DEFAULT_DATE_END,
    date_start: str = DEFAULT_DATE_START,
    login_pause: bool = False,
    click_nav_login: bool = True,
    simple_search_only: bool = False,
    max_pages: int = 1,
) -> list[dict[str, str]]:
    """
    阶段 1+2：检索 → 结果列表；max_pages>1 时翻页、去重、随机间隔。
    返回合并后的列表字典。
    """
    _setup_logging()
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    async with WenshuSpider(
        headless=headless,
        storage_state_path=storage_state_path,
    ) as spider:
        await spider.open_advanced_search()
        clicked_login = False
        if spider.page is not None:
            clicked_login = await maybe_click_nav_login(
                spider.page,
                want_click=click_nav_login,
            )
        if clicked_login and spider.page is not None:
            await wait_out_of_waf(spider.page)
            u = spider.page.url or ""
            if "open=login" in u or "181010CARHS5BS3C" in u:
                logger.info("登录链导致跳转，重新打开高级检索页")
                await spider.open_advanced_search()
        if login_pause:
            assert spider.page is not None
            logger.info(
                "WENSHU_LOGIN_PAUSE：请先在终端设置 PWDEBUG=1 再运行，"
                "以便打开 Inspector；在浏览器中完成登录/验证码后点 Resume 继续"
            )
            await spider.page.pause()
        assert spider.page is not None
        if simple_search_only:
            logger.info("开始简单搜索: keyword=%s", keyword)
            await apply_simple_home_search(spider.page, keyword=keyword)
        else:
            search_root = await resolve_search_root(spider.page)
            logger.info("开始筛选: keyword=%s", keyword)
            await spider.apply_filters(
                search_root,
                keyword=keyword,
                case_type=case_type,
                doc_type=doc_type,
                date_end=date_end,
                date_start=date_start,
            )
        list_root = await wait_results_anywhere(spider.page)
        mp = max(1, max_pages)
        items, _ = await crawl_result_list_pages(
            spider.page,
            list_root,
            max_pages=mp,
        )
        logger.info("多页合并后共 %d 条（去重后）", len(items))
        for i, row in enumerate(items[:50], 1):
            logger.info(
                "[%d] 案号=%s | 法院=%s | 日期=%s",
                i,
                row.get("案号", ""),
                row.get("受理法院", ""),
                row.get("裁判日期", ""),
            )
        if len(items) > 50:
            logger.info("… 其余 %d 条略", len(items) - 50)

        preview = Path(output_dir) / "wenshu_list_preview.json"
        preview.write_text(
            json.dumps(items, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("列表已写入 %s", preview)

        print(f"共爬取 {mp} 页（尽力），合并去重后 {len(items)} 条，已保存 {preview}")
        return items


def run_placeholder() -> None:
    """兼容旧入口；请改用 main 或 asyncio.run(run_phase1_list(...))。"""
    pass


if __name__ == "__main__":
    _setup_logging()
    h = os.environ.get("HEADLESS", "false").lower() in ("1", "true", "yes")
    st = os.environ.get("STORAGE_STATE_PATH", "").strip() or None
    pause = os.environ.get("WENSHU_LOGIN_PAUSE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    nav = os.environ.get("WENSHU_CLICK_NAV_LOGIN", "true").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    simple = os.environ.get("WENSHU_SIMPLE_SEARCH_ONLY", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    _mps = os.environ.get("MAX_PAGES", "1").strip()
    try:
        _mp = max(1, int(_mps)) if _mps else 1
    except ValueError:
        _mp = 1
    asyncio.run(
        run_phase1_list(
            headless=h,
            storage_state_path=st,
            login_pause=pause,
            click_nav_login=nav,
            simple_search_only=simple,
            max_pages=_mp,
        )
    )
