"""
文书网页面选择器集中配置。站点改版后请用 playwright codegen 更新此处，勿散落魔法字符串。

高级检索页（主文档；若遇 iframe 需在 spider 内改为 frame_locator）。
"""

ADVANCED_SEARCH_URL = (
    "https://wenshu.court.gov.cn/website/wenshu/181029CR4M5A62CH/index.html?"
)

SELECTORS: dict[str, str] = {
    # 顶栏「登录」：用于带 storage_state 时触发站点刷新已登录 UI（open=login 比纯文本更稳）
    "nav_login": 'a[onclick*="open=login"]',
    # 首页常只显示简单搜索，需先进入高级检索（文案以站点为准，可 codegen 更新）
    "advanced_search_entry": "text=高级检索",
    # 首页普通搜索：关键词框 + 右侧「搜索」div（非 button）
    "home_keyword_input": 'input[placeholder*="关键词"]',
    "home_search_click": "div.search-rightBtn.search-click",
    "keyword_input": "#keyValue1",
    "case_type_trigger": "#s2",
    "doc_type_trigger": "#s3",
    "date_start": "#dateRangeStart",
    "date_end": "#dateRangeEnd",
    "search_btn": "#searchBtn",
    # 分页（列表区或整页；改版后用 codegen 更新）
    "pagination_next": 'a:has-text("下一页")',
    # 结果列表：案件名称链接（用于等待与解析行）
    "result_case_link": "a.caseName",
    # 从链接回溯到表格行（若站点改为 div 布局，需 codegen 更新）
    "result_row_xpath": "xpath=ancestor::tr[1]",
}

# 详情页：依次等待，任一出现即认为可保存（改版后请 codegen 更新）
DETAIL_WAIT_SELECTORS: tuple[str, ...] = (
    "#content",
    "#Content",
    ".detail",
    ".detailContent",
    "div[id*='Detail']",
    "div[id*='content']",
    ".pdf_container",
)
