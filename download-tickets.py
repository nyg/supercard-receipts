"""
download-tickets.py

Opens https://www.supercard.ch/fr/appli-services-numerique/mes-achats.html
in the real Brave browser, driven by SeleniumBase's Pure CDP Mode in its
native async form (seleniumbase.undetected.cdp_driver.cdp_util.start_async),
pre-loaded with the session cookies currently stored in the local Brave
profile -- so an already-logged-in Brave session carries over.

WHY NOT JUST POINT AT THE LIVE BRAVE PROFILE DIRECTORY?
CDP Mode only supports a `user_data_dir` that a prior CDP Mode session
created. Pointing it at the everyday Brave profile breaks (profile lock
conflicts, "restore pages?" prompts, or an uncontrolled window). So:
  1. Read the session cookies out of the real Brave profile
     (`browser_cookie3` handles Brave's on-disk cookie decryption).
  2. Launch a fresh Brave instance under CDP control.
  3. Inject those cookies via Network.setCookies before navigating.

REQUIREMENTS
    pip install seleniumbase browser-cookie3

IMPORTANT
- Fully close Brave before running. Chromium-based browsers lock their
  cookie database while running, which makes extraction fail or return
  stale data.
- macOS will raise a Keychain prompt for "Brave Safe Storage" on the
  first run; approve it (Always Allow) or the read blocks.
- You must already be logged in to supercard.ch in Brave.
- On Windows with recent Chrome/Brave builds, app-bound encryption can
  defeat `browser_cookie3`. If it returns nothing, try `rookiepy`
  (`pip install rookiepy`, `rookiepy.brave(["supercard.ch"])`).
"""

import asyncio
import platform
from contextlib import suppress
from pathlib import Path

import browser_cookie3
from mycdp import network
from seleniumbase.undetected.cdp_driver import cdp_util

TARGET_URL = "https://www.supercard.ch/fr/appli-services-numerique/mes-achats.html"
COOKIE_DOMAIN = "supercard.ch"

SAME_SITE = {
    "strict": network.CookieSameSite.STRICT,
    "lax": network.CookieSameSite.LAX,
    "none": network.CookieSameSite.NONE,
}


def find_brave_binary() -> str | None:
    system = platform.system()
    if system == "Windows":
        candidates = [
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
            str(Path.home() / "AppData/Local/BraveSoftware/Brave-Browser/Application/brave.exe"),
        ]
    elif system == "Darwin":
        candidates = ["/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"]
    else:
        candidates = ["/usr/bin/brave-browser", "/opt/brave.com/brave/brave", "/snap/bin/brave"]

    for path in candidates:
        if Path(path).exists():
            return path
    return None


BRAVE_PATH = find_brave_binary()


def get_brave_cookies() -> list[network.CookieParam]:
    try:
        jar = browser_cookie3.brave(domain_name=COOKIE_DOMAIN)
    except Exception as e:
        raise SystemExit(
            "Could not read Brave's cookies. Make sure Brave is fully closed "
            "and that you're running as the same OS user as the Brave "
            f"profile.\nDetails: {e}"
        )

    params = []
    for c in jar:
        rest = {k.lower(): v for k, v in (c._rest or {}).items()}
        params.append(
            network.CookieParam(
                name=c.name,
                value=c.value or "",
                domain=c.domain,
                path=c.path or "/",
                secure=bool(c.secure),
                http_only="httponly" in rest,
                same_site=SAME_SITE.get(str(rest.get("samesite", "")).lower()),
                expires=(
                    network.TimeSinceEpoch(float(c.expires)) if c.expires else None
                ),
            )
        )
    return params


async def run() -> None:
    cookies = get_brave_cookies()
    if not cookies:
        raise SystemExit(
            "No cookies found for supercard.ch in Brave. Log in to "
            "supercard.ch in Brave, close Brave, then re-run."
        )

    kwargs = {}
    if BRAVE_PATH:
        kwargs["browser_executable_path"] = BRAVE_PATH
        print(f"Launching Brave from: {BRAVE_PATH}")
    else:
        print(
            "Could not auto-detect Brave; set BRAVE_PATH manually if this "
            "launches the wrong browser."
        )

    browser = await cdp_util.start_async(**kwargs)
    try:
        tab = await browser.get("https://www.supercard.ch/")
        await tab.wait(3)

        await browser.cookies.set_all(cookies)
        print(f"Injected {len(cookies)} cookie(s).")

        tab = await browser.get(TARGET_URL)
        await tab.wait(5)

        print("Page title:", await tab.get_title())
        print("Current URL:", await tab.get_current_url())

        with suppress(EOFError):
            await asyncio.get_running_loop().run_in_executor(
                None, input, "\nBrowser is open -- press Enter here to close it..."
            )
    finally:
        browser.stop()


if __name__ == "__main__":
    asyncio.run(run())
