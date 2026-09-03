import argparse
import asyncio
import base64
import datetime as dt
import json
import platform
import re
import shutil
from pathlib import Path

import browser_cookie3
from mycdp import network
from seleniumbase.undetected.cdp_driver import cdp_util

ORIGIN = "https://www.supercard.ch"
PURCHASES_PAGE = f"{ORIGIN}/fr/appli-services-numerique/mes-achats.html"
PURCHASES_API = "/bin/coop/supercard/digitalReceipt/getPurchases.json"
RECEIPT_API = "/bin/coop/kbk/kassenzettelpoc"
COOKIE_DOMAIN = "supercard.ch"

SAME_SITE = {
    "strict": network.CookieSameSite.STRICT,
    "lax": network.CookieSameSite.LAX,
    "none": network.CookieSameSite.NONE,
}

HOME = Path.home()

BROWSERS = {
    "brave": {
        "label": "Brave",
        "Darwin": ["/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"],
        "Windows": [
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
            str(HOME / "AppData/Local/BraveSoftware/Brave-Browser/Application/brave.exe"),
        ],
        "Linux": [
            "/usr/bin/brave-browser",
            "/usr/bin/brave",
            "/opt/brave.com/brave/brave",
            "/snap/bin/brave",
        ],
        "commands": ["brave-browser", "brave"],
    },
    "chrome": {
        "label": "Google Chrome",
        "Darwin": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
        "Windows": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            str(HOME / "AppData/Local/Google/Chrome/Application/chrome.exe"),
        ],
        "Linux": [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/opt/google/chrome/chrome",
        ],
        "commands": ["google-chrome", "google-chrome-stable"],
    },
    "chromium": {
        "label": "Chromium",
        "Darwin": ["/Applications/Chromium.app/Contents/MacOS/Chromium"],
        "Windows": [
            str(HOME / "AppData/Local/Chromium/Application/chrome.exe"),
            r"C:\Program Files\Chromium\Application\chrome.exe",
        ],
        "Linux": [
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/snap/bin/chromium",
        ],
        "commands": ["chromium", "chromium-browser"],
    },
    "edge": {
        "label": "Microsoft Edge",
        "Darwin": ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"],
        "Windows": [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ],
        "Linux": [
            "/usr/bin/microsoft-edge",
            "/usr/bin/microsoft-edge-stable",
            "/opt/microsoft/msedge/msedge",
        ],
        "commands": ["microsoft-edge", "microsoft-edge-stable"],
    },
    "vivaldi": {
        "label": "Vivaldi",
        "Darwin": ["/Applications/Vivaldi.app/Contents/MacOS/Vivaldi"],
        "Windows": [
            r"C:\Program Files\Vivaldi\Application\vivaldi.exe",
            str(HOME / "AppData/Local/Vivaldi/Application/vivaldi.exe"),
        ],
        "Linux": [
            "/usr/bin/vivaldi",
            "/usr/bin/vivaldi-stable",
            "/opt/vivaldi/vivaldi",
        ],
        "commands": ["vivaldi", "vivaldi-stable"],
    },
    "opera": {
        "label": "Opera",
        "Darwin": ["/Applications/Opera.app/Contents/MacOS/Opera"],
        "Windows": [str(HOME / "AppData/Local/Programs/Opera/opera.exe")],
        "Linux": ["/usr/bin/opera", "/snap/bin/opera"],
        "commands": ["opera"],
    },
    "opera_gx": {
        "label": "Opera GX",
        "Darwin": ["/Applications/Opera GX.app/Contents/MacOS/Opera"],
        "Windows": [str(HOME / "AppData/Local/Programs/Opera GX/opera.exe")],
        "Linux": [],
        "commands": [],
    },
    "arc": {
        "label": "Arc",
        "Darwin": ["/Applications/Arc.app/Contents/MacOS/Arc"],
        "Windows": [str(HOME / "AppData/Local/Packages/TheBrowserCompany.Arc/Arc.exe")],
        "Linux": [],
        "commands": [],
    },
    "firefox": {"label": "Firefox", "platforms": {"Darwin", "Linux", "Windows"}},
    "librewolf": {"label": "LibreWolf", "platforms": {"Darwin", "Linux", "Windows"}},
    "safari": {"label": "Safari", "platforms": {"Darwin"}},
}

CHROMIUM_BROWSERS = [
    name for name, spec in BROWSERS.items() if "commands" in spec
]

COOKIE_BROWSERS = [
    name for name in BROWSERS if hasattr(browser_cookie3, name)
]

AUTODETECT_ORDER = [
    "brave", "chrome", "firefox", "edge", "safari", "chromium",
    "vivaldi", "arc", "librewolf", "opera", "opera_gx",
]

FETCH_JSON_JS = """
(async () => {
  const r = await fetch(%s, {credentials: 'include'});
  if (!r.ok) return JSON.stringify({error: r.status});
  return JSON.stringify({data: await r.json()});
})()
"""

FETCH_PDF_JS = """
(async () => {
  const r = await fetch(%s, {credentials: 'include'});
  if (!r.ok) return JSON.stringify({error: r.status});
  const buf = new Uint8Array(await r.arrayBuffer());
  if (buf.length < 5 || String.fromCharCode(...buf.slice(0, 5)) !== '%%PDF-')
    return JSON.stringify({error: 'not a PDF'});
  let s = '';
  for (let i = 0; i < buf.length; i += 8192)
    s += String.fromCharCode(...buf.subarray(i, i + 8192));
  return JSON.stringify({b64: btoa(s)});
})()
"""


def label(browser: str) -> str:
    return BROWSERS[browser]["label"]


def supports_platform(browser: str) -> bool:
    system = platform.system()
    spec = BROWSERS[browser]
    if "platforms" in spec:
        return system in spec["platforms"]
    return bool(spec.get(system) or spec.get("commands"))


def find_browser_binary(browser: str) -> str | None:
    spec = BROWSERS[browser]
    if "commands" not in spec:
        return None
    for path in spec.get(platform.system(), []):
        if Path(path).exists():
            return path
    for command in spec["commands"]:
        found = shutil.which(command)
        if found:
            return found
    return None


def read_cookies(browser: str) -> list:
    return list(getattr(browser_cookie3, browser)(domain_name=COOKIE_DOMAIN))


def detect_cookie_browser() -> tuple[str, list]:
    tried = []
    for browser in AUTODETECT_ORDER:
        if browser not in COOKIE_BROWSERS or not supports_platform(browser):
            continue
        tried.append(label(browser))
        try:
            jar = read_cookies(browser)
        except Exception:
            continue
        if jar:
            return browser, jar

    raise SystemExit(
        f"No {COOKIE_DOMAIN} cookies found in any supported browser "
        f"(tried: {', '.join(tried)}).\nLog in to {ORIGIN} in one of them, "
        "close it, then re-run — or pass --cookies-from BROWSER explicitly."
    )


def to_cookie_params(jar) -> list[network.CookieParam]:
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


def collect_cookies(browser: str | None) -> tuple[str, list[network.CookieParam]]:
    if browser is None:
        browser, jar = detect_cookie_browser()
        return browser, to_cookie_params(jar)

    if not supports_platform(browser):
        raise SystemExit(f"{label(browser)} is not available on {platform.system()}.")

    try:
        jar = read_cookies(browser)
    except Exception as e:
        raise SystemExit(
            f"Could not read {label(browser)}'s cookies. Make sure it is fully "
            "closed and that you are running as the same OS user as its "
            f"profile.\nDetails: {e}"
        )

    if not jar:
        raise SystemExit(
            f"No {COOKIE_DOMAIN} cookies found in {label(browser)}. Log in to "
            f"{ORIGIN} there, close it, then re-run."
        )
    return browser, to_cookie_params(jar)


def resolve_driver(browser: str | None, path: str | None, cookie_browser: str) -> str:
    if path:
        if not Path(path).exists():
            raise SystemExit(f"--browser-path does not exist: {path}")
        return path

    if browser:
        found = find_browser_binary(browser)
        if not found:
            raise SystemExit(
                f"Could not find the {label(browser)} binary. Pass "
                "--browser-path /path/to/binary."
            )
        return found

    candidates = [cookie_browser] if cookie_browser in CHROMIUM_BROWSERS else []
    candidates += [b for b in AUTODETECT_ORDER if b in CHROMIUM_BROWSERS]
    for candidate in candidates:
        found = find_browser_binary(candidate)
        if found:
            return found

    raise SystemExit(
        "Could not find any Chromium-based browser to drive. Install one of "
        f"{', '.join(label(b) for b in CHROMIUM_BROWSERS)}, or pass "
        "--browser-path."
    )


async def fetch_json(tab, url: str) -> dict:
    raw = await tab.evaluate(FETCH_JSON_JS % json.dumps(url), await_promise=True)
    result = json.loads(raw)
    if "error" in result:
        raise RuntimeError(f"{url} returned {result['error']}")
    return result["data"]


async def fetch_pdf(tab, url: str) -> bytes | None:
    raw = await tab.evaluate(FETCH_PDF_JS % json.dumps(url), await_promise=True)
    result = json.loads(raw)
    if "error" in result:
        print(f"    skipped ({result['error']})")
        return None
    return base64.b64decode(result["b64"])


async def list_purchases(tab, date_from: dt.date, date_to: dt.date) -> list[dict]:
    purchases = []
    cursor = None
    while True:
        url = (
            f"{PURCHASES_API}?currentPage=0"
            f"&dateMin={date_from.isoformat()}&dateMax={date_to.isoformat()}"
        )
        if cursor:
            url += f"&endtimestamp={cursor}"

        page = await fetch_json(tab, url)
        batch = page.get("purchases") or []
        if not batch:
            break

        purchases.extend(batch)
        next_cursor = batch[-1].get("endtimestamp")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
    return purchases


def receipt_filename(purchase: dict, kind: str) -> str:
    stamp = purchase.get("endtimestamp") or ""
    when = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}_{stamp[8:14]}" if stamp else "unknown"
    shop = purchase.get("location", {}).get("name") or "unknown"
    shop = re.sub(r"[^\w-]+", "-", shop).strip("-") or "unknown"
    total = purchase.get("total", {}).get("amount")
    amount = f"_{total / 100:.2f}CHF" if isinstance(total, int) else ""
    suffix = "" if kind == "receipt" else f"_{kind}"
    return f"{when}_{shop}{amount}{suffix}.pdf"


async def download_all(tab, purchases: list[dict], out_dir: Path, warranties: bool) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = 0

    for index, purchase in enumerate(purchases, start=1):
        wanted = [("receipt", purchase.get("encBarcode"))]
        if warranties and purchase.get("hasWarranty") and not purchase.get("online"):
            wanted.append(("warranty", purchase.get("encBarcode")))

        for kind, barcode in wanted:
            if not barcode:
                continue
            target = out_dir / receipt_filename(purchase, kind)
            if target.exists():
                print(f"[{index}/{len(purchases)}] {target.name} (already there)")
                saved += 1
                continue

            print(f"[{index}/{len(purchases)}] {target.name}")
            pdf = await fetch_pdf(tab, f"{RECEIPT_API}?bc={barcode}&pdfType={kind}")
            if pdf:
                target.write_bytes(pdf)
                saved += 1
    return saved


def parse_args() -> argparse.Namespace:
    year = dt.date.today().year
    parser = argparse.ArgumentParser(
        description="Download supercard.ch digital receipts as PDFs."
    )
    parser.add_argument(
        "--from", dest="date_from", type=dt.date.fromisoformat,
        default=dt.date(year, 1, 1), metavar="YYYY-MM-DD",
        help="start of the range (default: January 1st of the current year)",
    )
    parser.add_argument(
        "--to", dest="date_to", type=dt.date.fromisoformat,
        default=dt.date(year, 12, 31), metavar="YYYY-MM-DD",
        help="end of the range (default: December 31st of the current year)",
    )
    parser.add_argument(
        "--out", dest="out_dir", type=Path, default=Path("tickets"),
        help="directory to write the PDFs into (default: ./tickets)",
    )
    parser.add_argument(
        "--warranties", action="store_true",
        help="also download the warranty PDF of purchases that have one",
    )
    parser.add_argument(
        "--cookies-from", dest="cookie_browser", choices=COOKIE_BROWSERS,
        metavar="BROWSER",
        help=(
            "browser whose supercard.ch session to reuse: "
            f"{', '.join(COOKIE_BROWSERS)} (default: the first one that has any)"
        ),
    )
    parser.add_argument(
        "--browser", dest="driver_browser", choices=CHROMIUM_BROWSERS,
        metavar="BROWSER",
        help=(
            "Chromium-based browser to drive: "
            f"{', '.join(CHROMIUM_BROWSERS)} (default: the cookie source when "
            "it is Chromium-based, else the first one installed)"
        ),
    )
    parser.add_argument(
        "--browser-path", dest="driver_path",
        help="explicit path to the browser binary to drive",
    )
    args = parser.parse_args()
    if args.date_from > args.date_to:
        parser.error("--from must not be later than --to")
    return args


async def run(args: argparse.Namespace) -> None:
    cookie_browser, cookies = collect_cookies(args.cookie_browser)
    print(f"Read {len(cookies)} cookie(s) from {label(cookie_browser)}.")

    driver_path = resolve_driver(
        args.driver_browser, args.driver_path, cookie_browser
    )
    print(f"Launching: {driver_path}")

    if cookie_browser not in CHROMIUM_BROWSERS or (
        args.driver_browser and args.driver_browser != cookie_browser
    ):
        print(
            f"Note: the cookies come from {label(cookie_browser)} but a "
            "different browser is being driven. DataDome ties its token to "
            "the browser fingerprint, so the purchases page may refuse to "
            "load. If it does, use a Chromium-based browser for both."
        )

    browser = await cdp_util.start_async(browser_executable_path=driver_path)
    try:
        tab = await browser.get(f"{ORIGIN}/")
        await tab.wait(3)

        await browser.cookies.set_all(cookies)
        print(f"Injected {len(cookies)} cookie(s).")

        tab = await browser.get(PURCHASES_PAGE)
        await tab.wait(5)

        if await tab.get_title() != "Mes achats":
            raise SystemExit(
                "The purchases page did not load as a logged-in session. Log "
                f"in to {ORIGIN} in {label(cookie_browser)}, close it, then "
                "re-run."
            )

        print(f"Listing purchases from {args.date_from} to {args.date_to}...")
        purchases = await list_purchases(tab, args.date_from, args.date_to)
        print(f"Found {len(purchases)} purchase(s).")

        saved = await download_all(tab, purchases, args.out_dir, args.warranties)
        print(f"\nSaved {saved} PDF(s) to {args.out_dir.resolve()}")
    finally:
        browser.stop()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
