# -*- coding: utf-8 -*-
"""
Render diagnostic & clicker for magmanode.com
- Reads env:
    MAGMANODE_SERVER_URL  (e.g. https://magmanode.com/server?id=770999)
    MAGMANODE_COOKIES_JSON (JSON array of cookies incl. PHPSESSID, cf_clearance, ...)
    MAGMANODE_UA (optional user-agent override)
"""

import os, json, time, traceback
from typing import List, Dict, Any

import requests
from flask import Flask, request, Response

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# --------- ENV ---------
SERVER_URL = os.getenv("MAGMANODE_SERVER_URL", "").strip()
UA = os.getenv("MAGMANODE_UA", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/139.0.0.0 Safari/537.36")
COOKIES_RAW = os.getenv("MAGMANODE_COOKIES_JSON", "").strip()

CHROME_BIN = "/usr/bin/chromium"
CHROMEDRIVER_BIN = "/usr/bin/chromedriver"

app = Flask("render_diag")


# --------- helpers ---------
def _load_cookies_env() -> List[Dict[str, Any]]:
    if not COOKIES_RAW:
        return []
    try:
        data = json.loads(COOKIES_RAW)
        if isinstance(data, dict):
            data = [data]
        # sanitize for Selenium (remove unknown samesite values)
        cleaned = []
        for c in data:
            c = dict(c)
            if c.get("sameSite", "").lower() not in ("lax", "strict", "none"):
                c.pop("sameSite", None)
            if "path" not in c:
                c["path"] = "/"
            if "domain" not in c:
                c["domain"] = "magmanode.com"
            cleaned.append(c)
        return cleaned
    except Exception:
        return []


def make_driver():
    opts = Options()
    # Headless & stability flags for Render
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1366,768")
    opts.add_argument("--lang=en-US")
    opts.add_argument(f"--user-agent={UA}")
    opts.binary_location = CHROME_BIN
    # capture network logs
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL", "browser": "ALL"})
    service = Service(executable_path=CHROMEDRIVER_BIN)
    driver = webdriver.Chrome(service=service, options=opts)
    driver.set_page_load_timeout(60)
    return driver


def inject_cookies(driver, cookies: List[Dict[str, Any]]):
    # must visit a URL on domain before add_cookie
    driver.get("https://magmanode.com/")
    for c in cookies:
        try:
            add = {
                "name": c["name"],
                "value": c.get("value", ""),
                "domain": c.get("domain", "magmanode.com"),
                "path": c.get("path", "/"),
                "secure": bool(c.get("secure", True)),
                "httpOnly": bool(c.get("httpOnly", False)),
                "expiry": c.get("expirationDate")  # optional
            }
            # remove None to avoid selenium complaints
            add = {k: v for k, v in add.items() if v is not None}
            driver.add_cookie(add)
        except Exception:
            pass


def buttons_present(driver):
    has_start = False
    has_stop = False
    try:
        driver.get(SERVER_URL)
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        # try by attribute (stable)
        has_start = len(driver.find_elements(By.CSS_SELECTOR, 'button[data-action="start"]')) > 0
        has_stop = len(driver.find_elements(By.CSS_SELECTOR, 'button[data-action="stop"]')) > 0
        # fallbacks
        if not has_start:
            has_start = len(driver.find_elements(By.XPATH, '//button[normalize-space()="START"]')) > 0
        if not has_stop:
            has_stop = len(driver.find_elements(By.XPATH, '//button[normalize-space()="STOP"]')) > 0
    except Exception:
        pass
    return has_start, has_stop


def click_action(driver, action: str) -> Dict[str, Any]:
    """
    Click start/stop; return info about selector + whether clicked.
    """
    info = {"clicked": False, "via": None, "selector": None}
    if action not in ("start", "stop"):
        return info

    sel_css = f'button[data-action="{action}"]'
    info["selector"] = {"by": "css selector", "selector": sel_css}

    try:
        btn = WebDriverWait(driver, 12).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, sel_css))
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        time.sleep(0.2)
        btn.click()
        info["clicked"] = True
        info["via"] = "native"
        return info
    except Exception:
        # fallback JS click by text
        try:
            btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, f'//button[normalize-space()="{action.upper()}"]'))
            )
            driver.execute_script("arguments[0].click();", btn)
            info["clicked"] = True
            info["via"] = "js"
        except Exception:
            pass
        return info


def collect_network(driver, limit=120) -> List[str]:
    """Return condensed network lines (REQ/RES with status and URL)."""
    out = []
    try:
        logs = driver.get_log("performance")
        for row in logs:
            msg = json.loads(row.get("message", "{}")).get("message", {})
            method = msg.get("method", "")
            params = msg.get("params", {})
            if method == "Network.requestWillBeSent":
                url = params.get("request", {}).get("url", "")
                out.append(f"REQ {params.get('request', {}).get('method','GET')} {url}")
            elif method == "Network.responseReceived":
                url = params.get("response", {}).get("url", "")
                status = params.get("response", {}).get("status", 0)
                out.append(f"RES {status} {url}")
            if len(out) >= limit:
                break
    except Exception:
        pass
    return out


def check_status_with_requests(cookies: List[Dict[str, Any]]) -> int:
    """Return HTTP status of SERVER_URL using Requests with same cookies/UA."""
    try:
        s = requests.Session()
        for c in cookies:
            s.cookies.set(c["name"], c.get("value", ""), domain=c.get("domain", "magmanode.com"), path=c.get("path", "/"))
        r = s.get(SERVER_URL, headers={"User-Agent": UA}, timeout=25, allow_redirects=True)
        return r.status_code
    except Exception:
        return -1


def run_diag(action: str) -> Dict[str, Any]:
    result = {
        "ok": True,
        "action": action or "",
        "server_url": SERVER_URL,
        "env_ok": bool(SERVER_URL),
        "cookies_count": 0,
        "cookies_error": None,
        "has_start": False,
        "has_stop": False,
        "status_before": "unknown",
        "status_after": "unknown",
        "selector": None,
        "note": "",
        "network": [],
    }

    cookies = _load_cookies_env()
    result["cookies_count"] = len(cookies)

    # quick status before (using requests with same cookies/UA)
    result["status_before"] = check_status_with_requests(cookies)

    if not SERVER_URL:
        return result

    drv = None
    try:
        drv = make_driver()
        inject_cookies(drv, cookies)

        # open target page
        drv.get(SERVER_URL)
        WebDriverWait(drv, 30).until(EC.presence_of_element_located((By.TAG_NAME, "body")))

        has_start, has_stop = buttons_present(drv)
        result["has_start"], result["has_stop"] = has_start, has_stop

        # click if requested
        if action in ("start", "stop") and (has_start or has_stop):
            info = click_action(drv, action)
            result["selector"] = info.get("selector")
            if info.get("clicked"):
                result["note"] = f"clicked=True via={info.get('via')}"
            else:
                result["note"] = "click failed"

            # tiny wait to let any XHR fire
            time.sleep(2)

        # collect network and status after
        result["network"] = collect_network(drv, limit=200)
        result["status_after"] = check_status_with_requests(cookies)

    except Exception as e:
        result["ok"] = True  # still render page; diag page itself is OK
        result["note"] = f"driver-error: {repr(e)}"
    finally:
        if drv:
            try:
                drv.quit()
            except Exception:
                pass

    return result


# --------- Flask routes ---------
@app.route("/")
def home():
    return (
        "<h1>Render Diagnostic</h1>"
        "<p>رفتن به گزارش: <a href='/diag'>/diag</a></p>"
        "<p>JSON: <a href='/diag?format=json'>/diag?format=json</a></p>"
        "<p>کلیک و گزارش همزمان: "
        "<a href='/diag?action=start'>start</a> | "
        "<a href='/diag?action=stop'>stop</a></p>"
        "<p>هویت مرورگر: <a href='/whoami'>/whoami</a></p>"
    )


@app.route("/diag")
def diag():
    action = request.args.get("action", "").strip().lower()
    want_json = request.args.get("format", "").lower() == "json"

    try:
        result = run_diag(action)
    except Exception:
        # last-resort catch to never 5xx
        result = {"ok": False, "error": traceback.format_exc()}

    if want_json:
        return Response(json.dumps(result, ensure_ascii=False), mimetype="application/json")

    # minimal plaintext report (short)
    lines = [
        "Diag",
        f"ok: {result.get('ok')}",
        "",
        f"action: {result.get('action')}",
        f"server_url: {result.get('server_url')}",
        f"cookies_count: {result.get('cookies_count')}",
        f"has_start: {result.get('has_start')} | has_stop: {result.get('has_stop')}",
    ]
    if "status_before" in result:
        lines.append(f"status_before: {result.get('status_before')}")
    if "status_after" in result:
        lines.append(f"status_after: {result.get('status_after')}")
    if result.get("selector"):
        lines.append(f"selector: {result['selector']}")
    if result.get("note"):
        lines.append(f"note: {result['note']}")

    # show a few network lines
    if result.get("network"):
        lines.append("network:")
        for ln in result["network"][:40]:
            lines.append(f"- {ln}")

    lines.append("")
    lines.append("JSON | /api/status")
    body = "\n".join(lines)
    return Response(body, mimetype="text/plain; charset=utf-8")


@app.route("/whoami")
def whoami():
    info = {
        "env_ok": bool(SERVER_URL),
        "cookies_count": len(_load_cookies_env()),
        "ua": UA,
    }
    return Response(json.dumps(info, ensure_ascii=False), mimetype="application/json")


@app.route("/api/status")
def api_status():
    out = {
        "service": "render_diag",
        "version": "v2",
        "ok": True,
        "pages": {
            "self": {"url": request.host_url, "status": 200, "ok": True},
            "magma": {"url": SERVER_URL, "status": -1, "ok": False},
        },
    }
    try:
        r = requests.get(SERVER_URL, headers={"User-Agent": UA}, timeout=20, allow_redirects=True)
        out["pages"]["magma"]["status"] = r.status_code
        out["pages"]["magma"]["ok"] = (r.status_code < 500 and r.status_code != -1)
    except Exception:
        pass
    return Response(json.dumps(out, ensure_ascii=False), mimetype="application/json")


if __name__ == "__main__":
    # For local testing
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), debug=False)
