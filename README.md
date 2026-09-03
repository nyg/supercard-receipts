# coop-supercard

Downloads your digital till receipts (*tickets de caisse*) from [supercard.ch](https://www.supercard.ch) as PDFs, one file per purchase, filtered by date range.

Supercard only offers receipts one at a time behind a "show more" button, and the page is protected by DataDome bot detection. This script sidesteps both by reusing the session you already have in Brave: it reads the supercard.ch cookies out of your local Brave profile, injects them into a fresh browser instance under DevTools Protocol control, then calls the site's own JSON endpoints from inside the page.

## Requirements

- Python 3.10+
- [Brave](https://brave.com), already logged in to supercard.ch

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Usage

**Close Brave first.** Chromium-based browsers hold a lock on their cookie database while running, which makes the cookie read fail or return stale data.

Current calendar year, into `./tickets`:

```bash
.venv/bin/python download-tickets.py
```

An explicit range:

```bash
.venv/bin/python download-tickets.py --from 2025-01-01 --to 2025-12-31
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--from YYYY-MM-DD` | January 1st of the current year | Start of the range |
| `--to YYYY-MM-DD` | December 31st of the current year | End of the range |
| `--out DIR` | `./tickets` | Where to write the PDFs |
| `--warranties` | off | Also fetch the guarantee PDF of purchases that have one |

Files are named `2026-08-28_201015_Crissier_84.95CHF.pdf`. Existing files are left alone, so an interrupted run resumes where it stopped.

On the first run macOS raises a Keychain prompt for **Brave Safe Storage**. Approve it (*Always Allow*) or the cookie read blocks indefinitely.

## How it works

1. `browser_cookie3` reads and decrypts the supercard.ch cookies from the Brave profile on disk.
2. A fresh Brave instance starts under SeleniumBase's Pure CDP Mode, using its native async API (`cdp_util.start_async`).
3. The cookies go in through `Network.setCookies`, which carries over both the login session (`oauthTokens`) and the already-validated `datadome` token.
4. The purchases page is loaded, and the site's own endpoints are called from inside it with `fetch(..., {credentials: 'include'})`, so every request looks exactly like one the page itself made.

The two endpoints, both reverse-engineered from the page's script at `libs.coop.ch/digital-receipt/prod/script-foot.js`:

| Endpoint | Purpose |
| --- | --- |
| `/bin/coop/supercard/digitalReceipt/getPurchases.json` | Listing. Takes `dateMin` / `dateMax` as `YYYY-MM-DD`. |
| `/bin/coop/kbk/kassenzettelpoc?bc=<encBarcode>&pdfType=receipt` | The PDF for one purchase. |

## Notes and gotchas

Some of these cost real time to rediscover, so they are written down here.

**Pagination is cursor-based, not page-based.** `getPurchases.json` accepts a `currentPage` parameter, but it does nothing past the first page — the site itself always sends `currentPage: 0` and instead passes the last item's `endtimestamp` to get the next batch. Page size is 20.

**Cookies must be `mycdp.network.CookieParam` objects, with `expires` wrapped in `network.TimeSinceEpoch`.** `CookieParam.to_json()` calls `self.expires.to_json()`, which a plain `float` does not have, and `Network.setCookies` calls `to_json()` on every entry, so plain dicts fail the same way. Both failures are easy to miss: they surface as cookies silently not being set.

**`Network.setCookies` is a no-op on `about:blank`.** The browser navigates to the domain before injecting, and only then to the target page.

**Without valid cookies the site serves a DataDome interstitial** — a page whose body is empty and which quietly loads `geo.captcha-delivery.com`. If you see an empty page instead of receipts, the cookies did not make it in.

**Do not point CDP Mode at your live Brave profile directory.** `user_data_dir` is only supported when a prior CDP Mode session created it; the everyday profile gives you lock conflicts, "restore pages?" prompts, or a window that is not actually under control.

**On Windows**, recent Chrome/Brave builds use app-bound encryption, which `browser_cookie3` cannot read. If it comes back empty, try [`rookiepy`](https://pypi.org/project/rookiepy/) (`rookiepy.brave(["supercard.ch"])`) instead.
