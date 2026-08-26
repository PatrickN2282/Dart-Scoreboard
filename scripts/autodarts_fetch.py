#!/usr/bin/env python3
"""
Autodarts crawler: loggt sich über die Web-Oberfläche ein (Email/Passwort),
liest Match-IDs von /history/matches und ruft für jede Match-ID die
Stats-API /as/v0/matches/<id>/stats ab.

Voraussetzungen:
- Python 3.9+
- Playwright (pip install playwright) und playwright installiert via `playwright install`

Ausführung (Beispiel):
    export AD_EMAIL='support@smartps.de'
    export AD_PASS='Smart2026!'
    python3 scripts/autodarts_fetch.py --max-pages 2

Hinweis: Script öffnet einen Headless-Browser und verwendet die Web-Session
um die API-Requests auszuführen (Cookies/Session werden vom Browser gesetzt).
"""

import os
import time
import argparse
import json
from playwright.sync_api import sync_playwright

LOGIN_URL = "https://play.autodarts.com/auth/login"
HISTORY_URL = "https://play.autodarts.com/history/matches"
API_STATS_TPL = "https://api.autodarts.com/as/v0/matches/{match_id}/stats"


def login_and_get_session(email: str, password: str, headless: bool = True, user_data_dir: str | None = None):
    p = sync_playwright().start()
    browser = p.chromium.launch_persistent_context(user_data_dir or None, headless=headless)
    page = browser.new_page()

    # Try direct navigation to the history page: if already logged in, it will show content
    page.goto(HISTORY_URL)
    time.sleep(1)

    # If redirected to login, try login flow
    if page.url.startswith("https://play.autodarts.com/auth") or "sign in" in page.title().lower() or "Sign in" in page.content()[:200]:
        print("Login required; attempting credential login...")
        page.goto("https://play.autodarts.com/auth/login")
        # Try common selectors; site may use different names
        try:
            page.fill('input[name="email"]', email)
            page.fill('input[name="password"]', password)
            page.click('button[type="submit"]')
        except Exception:
            # fallback guesses
            try:
                page.fill('input[type="email"]', email)
                page.fill('input[type="password"]', password)
                page.click('button[type="submit"]')
            except Exception as e:
                print("Automated login failed: could not find login form selectors.", e)
                browser.close()
                p.stop()
                return None, None

        # wait for navigation after login
        page.wait_for_load_state('networkidle', timeout=15000)
        print("Login flow finished, current page:", page.url)

    # Now ensure we are on history page
    page.goto(HISTORY_URL)
    page.wait_for_load_state('networkidle')
    return p, browser, page


def collect_match_ids(page, max_pages=3):
    match_ids = []
    for page_no in range(max_pages):
        url = HISTORY_URL if page_no == 0 else HISTORY_URL + f"?page={page_no}"
        print("Loading:", url)
        page.goto(url)
        page.wait_for_load_state('networkidle')
        content = page.content()

        # Extract hrefs to matches via DOM evaluation (links containing /history/matches/<id>)
        links = page.eval_on_selector_all('a[href*="/history/matches/"]', 'els => els.map(e => e.getAttribute("href"))')
        for l in links:
            if "/history/matches/" in l:
                parts = l.rstrip('/').split('/')
                match_id = parts[-1]
                if match_id and match_id not in match_ids:
                    match_ids.append(match_id)
        # crude stop if none found on page
        if not links:
            break
        time.sleep(0.2)
    return match_ids


def fetch_stats_for_match(page, match_id):
    api_url = API_STATS_TPL.format(match_id=match_id)
    print("Fetching stats for:", match_id)
    # Use window.fetch via page.evaluate so cookies are included
    try:
        result = page.evaluate("(url) => fetch(url).then(r => r.ok ? r.json() : {status:r.status}).catch(e => ({error:e.toString()}))", api_url)
        return result
    except Exception as e:
        return {"error": str(e)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--email', default=os.getenv('AD_EMAIL'))
    parser.add_argument('--password', default=os.getenv('AD_PASS'))
    parser.add_argument('--max-pages', type=int, default=2)
    parser.add_argument('--headless', action='store_true')
    args = parser.parse_args()

    if not args.email or not args.password:
        print("Email/Password required via --email/--password or AD_EMAIL/AD_PASS env vars.")
        raise SystemExit(1)

    p, browser, page = login_and_get_session(args.email, args.password, headless=not args.headless)
    if not page:
        print("Login failed or could not create browser context.")
        raise SystemExit(2)

    try:
        ids = collect_match_ids(page, max_pages=args.max_pages)
        print(f"Collected {len(ids)} match ids:", ids)

        stats = {}
        for mid in ids[:10]:
            stats[mid] = fetch_stats_for_match(page, mid)
            # small delay
            time.sleep(0.2)

        print(json.dumps(stats, indent=2, ensure_ascii=False))

    finally:
        try:
            browser.close()
            p.stop()
        except Exception:
            pass
