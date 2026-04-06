"""
一次性保存文书网登录态，供 main.py 通过 STORAGE_STATE_PATH 复用。

用法（务必有界面、非 headless）：
  cd 项目根目录 && source .venv/bin/activate
  python save_storage_state.py

默认写入 ./data/wenshu_state.json，也可：
  STORAGE_STATE_PATH=./data/my_state.json python save_storage_state.py
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from playwright.async_api import async_playwright

# 首页：可改为你在浏览器里实际完成登录的入口 URL
START_URL = os.environ.get("WENSHU_START_URL", "https://wenshu.court.gov.cn/")


async def main() -> None:
    out = Path(
        os.environ.get("STORAGE_STATE_PATH", "data/wenshu_state.json"),
    ).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(120_000)

        print(f"正在打开: {START_URL}")
        await page.goto(START_URL, wait_until="load")

        print()
        print("—— 请在本机已弹出的 Chromium 窗口中完成：登录、验证码等 ——")
        print("—— 确认能正常看到文书网页面（例如能进检索）后，再回到此终端 ——")
        print()
        await asyncio.to_thread(
            input,
            f"完成后按 Enter，将把登录态保存到:\n  {out}\n> ",
        )

        await context.storage_state(path=str(out))
        await browser.close()

    print(f"已保存。请在 .env 中设置: STORAGE_STATE_PATH={out}")


if __name__ == "__main__":
    asyncio.run(main())
