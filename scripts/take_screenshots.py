"""Generate README screenshots for ClipMind (dev tool, not shipped)."""

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8378"
OUT = Path(__file__).resolve().parents[1] / "docs"
OUT.mkdir(exist_ok=True)


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(
            viewport={"width": 1180, "height": 760},
            device_scale_factor=2,
        )
        await page.goto(BASE, wait_until="networkidle")
        await page.wait_for_timeout(900)

        # --- shot 1: the history feed ---
        await page.screenshot(path=str(OUT / "screenshot-history.png"))

        # --- shot 2: intent search hitting the OTP clip ---
        q = page.locator("#q")
        await q.click()
        await q.type("验证码", delay=40)
        await page.wait_for_timeout(700)
        await page.screenshot(path=str(OUT / "screenshot-search-otp.png"))

        # --- shot 3: entity one-tap copy (type in a URL query) ---
        await q.fill("")
        await q.type("github", delay=40)
        await page.wait_for_timeout(700)
        await page.screenshot(path=str(OUT / "screenshot-search-link.png"))

        # --- shot 4: pure-intent recall: ask for "颜色" ---
        await q.fill("")
        await q.type("颜色", delay=40)
        await page.wait_for_timeout(700)
        await page.screenshot(path=str(OUT / "screenshot-search-color.png"))

        await browser.close()
        print("saved:", *[p.name for p in sorted(OUT.glob("*.png"))], sep="\n  ")


if __name__ == "__main__":
    asyncio.run(main())
