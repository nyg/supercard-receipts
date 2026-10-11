# coop-supercard

Downloads your digital till receipts (*tickets de caisse*) from [supercard.ch](https://www.supercard.ch) as PDFs, one file per purchase, filtered by date range.

Supercard only offers receipts one at a time behind a "show more" button, and the page is protected by DataDome bot detection. This script sidesteps both by reusing the session you already have in your everyday browser: it reads the supercard.ch cookies out of your local browser profile, injects them into a fresh browser instance under DevTools Protocol control, then calls the site's own JSON endpoints from inside the page.

## Requirements

- Python 3.10+
- A browser you are already logged in to supercard.ch with (see [Browser support](#browser-support))

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Usage

**Close the browser you take the cookies from first.** Browsers hold a lock on their cookie database while running, which makes the cookie read fail or return stale data.

Current calendar year, into `./tickets`, auto-detecting which browser holds a supercard.ch session:

```bash
.venv/bin/python download-tickets.py
```

An explicit range and an explicit browser:

```bash
.venv/bin/python download-tickets.py --from 2025-01-01 --to 2025-12-31 --cookies-from firefox
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--from YYYY-MM-DD` | January 1st of the current year | Start of the range |
| `--to YYYY-MM-DD` | December 31st of the current year | End of the range |
| `--out DIR` | `./tickets` | Where to write the PDFs |
| `--warranties` | off | Also fetch the guarantee PDF of purchases that have one |
| `--cookies-from BROWSER` | first browser found holding a supercard.ch session | Which browser's session to reuse |
| `--browser BROWSER` | the cookie source if it is Chromium-based, else the first Chromium browser installed | Which Chromium-based browser to drive |
| `--browser-path PATH` | auto-detected | Explicit path to the browser binary to drive, when auto-detection picks the wrong one |

Files are named `<date>_<time>_<store>_<total>CHF.pdf`, for example `2024-03-15_181200_Lausanne_42.50CHF.pdf`. Existing files are left alone, so an interrupted run resumes where it stopped.

## Browser support

Two different browsers are involved, and they do not have to be the same one.

**The cookie source** — where your supercard.ch login lives. Anything [`browser_cookie3`](https://pypi.org/project/browser-cookie3/) can read: Brave, Chrome, Chromium, Edge, Vivaldi, Opera, Opera GX, Arc, Firefox, LibreWolf, Safari.

**The driven browser** — the fresh instance the script launches under CDP control. Must be Chromium-based: Brave, Chrome, Chromium, Edge, Vivaldi, Opera, Opera GX, Arc. Firefox and Safari cannot be driven this way; SeleniumBase's Pure CDP Mode speaks the Chrome DevTools Protocol only. Firefox's remote protocol does not expose the equivalent stealth surface, and Safari has no CDP at all.

**Mixing the two mostly does not work.** DataDome binds its token to the browser fingerprint that solved the challenge, so a `datadome` cookie minted in Firefox or Safari is likely to be rejected when replayed from Chromium — you get the interstitial instead of the purchases page. The script warns when it detects a mismatch. If you hit it, log in to supercard.ch in a Chromium-based browser and use that one for both.

## OS support

macOS, Linux and Windows. The cookie decryption is what differs per platform.

| | macOS | Linux | Windows |
| --- | --- | --- | --- |
| Chromium-based | Keychain prompt on first run | `gnome-keyring` / `kwallet`, or the hardcoded fallback key | App-bound encryption may block reads (see below) |
| Firefox / LibreWolf | works | works | works |
| Safari | needs Full Disk Access | n/a | n/a |

- **macOS** raises a Keychain prompt for *"\<Browser\> Safe Storage"* on the first run. Approve it (*Always Allow*) or the cookie read blocks indefinitely. For Safari, grant your terminal **Full Disk Access** in *System Settings → Privacy & Security*, otherwise the cookie file is unreadable.
- **Linux** may prompt the desktop keyring for the browser's encryption key. Browsers started with `--password-store=basic` use a fixed key instead and need no prompt.
- **Windows**: recent Chrome/Brave/Edge builds use app-bound encryption, which `browser_cookie3` cannot read. If it comes back empty, either use Firefox as the cookie source or try [`rookiepy`](https://pypi.org/project/rookiepy/) (`rookiepy.brave(["supercard.ch"])`).

## How it works

1. `browser_cookie3` reads and decrypts the supercard.ch cookies from the browser profile on disk.
2. A fresh Chromium-based browser starts under SeleniumBase's Pure CDP Mode, using its native async API (`cdp_util.start_async`).
3. The cookies go in through `Network.setCookies`, which carries over the site session (`CMSSESSIONID`), the Supercard ID single sign-on cookie (`TGC` on `login.supercard.ch`) and the already-validated `datadome` token.
4. The purchases page is loaded, and the site's own endpoints are called from inside it with `fetch(..., {credentials: 'include'})`, so every request looks exactly like one the page itself made.
5. If the listing is refused because the site session has expired, the script follows the page's own login link once. Supercard ID recognises the single sign-on cookie, sends the browser straight back, and the listing is retried.

The two endpoints, both reverse-engineered from the page's script at `libs.coop.ch/digital-receipt/prod/script-foot.js`:

| Endpoint | Purpose |
| --- | --- |
| `/bin/coop/supercard/digitalReceipt/getPurchases.json` | Listing. Takes `dateMin` / `dateMax` as `YYYY-MM-DD`. Answers `401` without a logged-in session. |
| `/bin/coop/kbk/kassenzettelpoc?bc=<encBarcode>&pdfType=receipt` | The PDF for one purchase. |

## Notes and gotchas

Some of these cost real time to rediscover, so they are written down here.

**Pagination is cursor-based, not page-based.** `getPurchases.json` accepts a `currentPage` parameter, but it does nothing past the first page — the site itself always sends `currentPage: 0` and instead passes the last item's `endtimestamp` to get the next batch. Page size is 20.

**Cookies must be `mycdp.network.CookieParam` objects, with `expires` wrapped in `network.TimeSinceEpoch`.** `CookieParam.to_json()` calls `self.expires.to_json()`, which a plain `float` does not have, and `Network.setCookies` calls `to_json()` on every entry, so plain dicts fail the same way. Both failures are easy to miss: they surface as cookies silently not being set.

**`Network.setCookies` is a no-op on `about:blank`.** The browser navigates to the domain before injecting, and only then to the target page.

**The purchases page says nothing about whether you are logged in.** Anonymous visitors get the same page at the same URL with the same title (`Mes achats – Supercard`), just without receipts in it. The script used to compare the title to detect a login and broke the day the site renamed it, so the login check is now the listing endpoint itself: a `401` or `403` from `getPurchases.json` means the session was not accepted. The page has also moved once already, from `/fr/appli-services-numerique/mes-achats.html` to `/fr/profiter/avantages/mes-achats.html`; the old address still redirects.

**Being logged in in your browser does not mean the cookies on disk hold a live session.** The site session is a `CMSSESSIONID` session cookie on `www.supercard.ch` that stops being accepted a short while after your last page view. Your browser hides this, because the long-lived `TGC` cookie on `login.supercard.ch` signs you back in whenever you return. Replaying only the cookies therefore works minutes after a visit and gets a `401` later. That is why the script signs in again through the page's `_loginLink` (an OIDC authorize URL on `login.supercard.ch/cas`) when the listing is refused. If Supercard ID shows its login form instead of bouncing back, the single sign-on cookie is gone too and you have to log in again in your browser.

**Without valid cookies the site serves a DataDome interstitial** — a page whose body is empty and which quietly loads `geo.captcha-delivery.com`. If you see an empty page instead of receipts, the cookies did not make it in.

**Do not point CDP Mode at your live browser profile directory.** `user_data_dir` is only supported when a prior CDP Mode session created it; the everyday profile gives you lock conflicts, "restore pages?" prompts, or a window that is not actually under control. This is why the cookies are extracted and re-injected rather than reused in place.
