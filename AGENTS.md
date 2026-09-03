# AGENTS.md

## What this is

A single-file Python script, [`download-tickets.py`](download-tickets.py), that downloads digital till receipts from supercard.ch as PDFs. No package, no framework, no tests. `README.md` is the user-facing documentation and the place where hard-won protocol details are recorded; keep it in sync when behaviour changes.

## Environment

The virtualenv lives in `.venv/` and is not managed by any tool beyond `pip`. Always invoke it explicitly — there is no activation step in agent shells:

```bash
.venv/bin/python download-tickets.py --help
```

Add dependencies by pinning an exact version in `requirements.txt`, then `.venv/bin/pip install -r requirements.txt`.

## Running it

**Do not run the script end to end unattended.** A real run launches a browser window, reads the user's actual browser cookies (which triggers a macOS Keychain prompt), hits supercard.ch with the user's live session and writes PDFs. Ask before doing that.

Safe things to run without asking: `--help`, syntax checks, and importing the module to exercise the pure functions (`find_browser_binary`, `supports_platform`, `resolve_driver`, `receipt_filename`). Note that `detect_cookie_browser`, `read_cookies` and `collect_cookies` touch the real profile and are *not* in that safe set.

```bash
.venv/bin/python -c "
import importlib.util
spec = importlib.util.spec_from_file_location('dt', 'download-tickets.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print(m.find_browser_binary('brave'))
"
```

The filename has a hyphen, so it is not importable as a module name — use `importlib` as above.

## Code conventions

No comments and no docstrings, anywhere. Intent goes in names and structure; rationale goes in the commit body or in `README.md`.

Beyond that, match what is already there: standard-library-first imports, module-level constants in `UPPER_SNAKE`, `async def` for anything touching the CDP tab, `SystemExit` with an actionable message for user-facing failures (never a bare traceback), f-strings, type hints on function signatures.

## Structure

Roughly top to bottom: constants and the `BROWSERS` registry, browser discovery, cookie extraction, CDP fetch helpers, the Supercard API calls, filename building, argument parsing, and `run()`.

`BROWSERS` is the one table to edit when adding browser support. An entry with a `commands` key is Chromium-based and can be *driven*; one with a `platforms` key can only be a *cookie source*. Anything `browser_cookie3` exposes as a same-named function is automatically offered to `--cookies-from`.

## Things that will bite you

These are load-bearing and were expensive to find. `README.md` explains each in full; do not "clean up" any of them without reading it first.

- Cookies must be `mycdp.network.CookieParam` objects with `expires` wrapped in `network.TimeSinceEpoch`. Plain dicts or floats fail silently.
- `Network.setCookies` is a no-op on `about:blank`, so the browser navigates to the origin before injecting.
- `getPurchases.json` pagination is cursor-based via the last item's `endtimestamp`. The `currentPage` parameter does nothing past page 0.
- Never point CDP Mode at a live browser profile via `user_data_dir`. That is the entire reason cookies are extracted and re-injected.
- Requests are made with in-page `fetch(..., {credentials: 'include'})` rather than a Python HTTP client, so DataDome sees a genuine same-origin request from the page.

## Git

Default branch is `master`. Commit messages: short imperative subject, no trailing period; the body carries the rationale that would otherwise be a comment.
