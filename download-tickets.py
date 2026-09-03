"""
download-tickets.py

Downloads the digital till receipts (tickets de caisse) from
supercard.ch as PDFs, for a given date range.

It drives a fresh Brave instance through SeleniumBase's Pure CDP Mode
(native async API) and injects the session cookies read out of the local
Brave profile, so an already-logged-in Brave session carries over.

USAGE
    python download-tickets.py                          # current year
    python download-tickets.py --from 2024-01-01 --to 2024-06-30
    python download-tickets.py --out ~/receipts --warranties

WHY NOT JUST POINT AT THE LIVE BRAVE PROFILE DIRECTORY?
CDP Mode only supports a `user_data_dir` that a prior CDP Mode session
created. Pointing it at the everyday Brave profile breaks (profile lock
conflicts, "restore pages?" prompts, or an uncontrolled window). So:
  1. Read the session cookies out of the real Brave profile
     (`browser_cookie3` handles Brave's on-disk cookie decryption).
  2. Launch a fresh Brave instance under CDP control.
  3. Inject those cookies via Network.setCookies before navigating.

REQUIREMENTS
    pip install -r requirements.txt

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

import argparse
import asyncio
import base64
import datetime as dt
import json
import platform
import re
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
    args = parser.parse_args()
    if args.date_from > args.date_to:
        parser.error("--from must not be later than --to")
    return args


async def run(args: argparse.Namespace) -> None:
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
        tab = await browser.get(f"{ORIGIN}/")
        await tab.wait(3)

        await browser.cookies.set_all(cookies)
        print(f"Injected {len(cookies)} cookie(s).")

        tab = await browser.get(PURCHASES_PAGE)
        await tab.wait(5)

        if await tab.get_title() != "Mes achats":
            raise SystemExit(
                "The purchases page did not load as a logged-in session. Log "
                "in to supercard.ch in Brave, close Brave, then re-run."
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
