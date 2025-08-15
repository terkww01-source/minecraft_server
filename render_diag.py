# -*- coding: utf-8 -*-
import os, json, time, traceback
from typing import List, Dict, Any

import requests
from flask import Flask, request, Response

# مهم: به جای selenium معمولی از undetected-chromedriver استفاده می‌کنیم
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

SERVER_URL = os.getenv("MAGMANODE_SERVER_URL", "").strip()
COOKIES_RAW = os.getenv("MAGMANODE_COOKIES_JSON", "").strip()
UA = os.getenv("MAGMANODE_UA", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/139.0.0.0 Safari/537.36")
CHROME_BIN = "/usr/bin/chromium"  # روی Render نصب است

app = Flask("render_diag")

# ---------- helpers ----------
def _load_cookies_env() -> List[Dict[str, Any]]:
    if not COOKIES_RAW:
        return []
    try:
        data = json.loads(COOKIES_RAW)
        if isinstance(data, dict):
            data = [data]
        cleaned = []
        for c in data:
            c = dict(c)
            if c.get("sameSite", "").lower() not in ("lax", "strict", "none"):
                c.pop("sameSite", None)
            if "path" not in c: c["path"] = "/"
            if "domain" not in c: c["domain"] = "magmanode.com"
            cleaned.append(c)
        return cleaned
    except Exception:
        return []

def make_driver():
    opts = uc.ChromeOptions()
    # پنهان‌سازی ردپای اتوماسیون
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1366,768")
    opts.add_argument("--lang=en-US")
    opts.add_argument(f"--user-agent={UA}")
    # لاگ شبکه برای دیباگ
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL", "browser": "ALL"})
    driver = uc.Chrome(options=opts, browser_executable_path=CHROME_BIN, use_subprocess=True)
    driver.set_page_load_timeout(60)
    return driver

def inject_cookies(driver, cookies: List[Dict[str, Any]]):
    # باید اول وارد دامنه شویم
    driver.get("https://magmanode.com/")
    time.sleep(0.8)
    for c in cookies:
        try:
            add = {
                "name": c["name"],
                "value": c.get("value", ""),
                "domain": c.get("domain", "magmanode.com"),
                "path": c.get("path", "/"),
                "secure": bool(c.get("secure", True)),
                "httpOnly": bool(c.get("httpOnly", False)),
                "expiry": c.get("expirationDate")
            }
            add = {k: v for k, v in add.items() if v is not None}
            driver.add_cookie(add)
        except Exception:
            pass

def buttons_present(driver):
    has_start = len(driver.find_elements(By.CSS_SELECTOR, 'button[data-action="start"]')) > 0
    has_stop  = len(driver.find_elements(By.CSS_SELECTOR, 'button[data-action="stop"]')) > 0
    if not has_start:
        has_start = len(driver.find_elements(By.XPATH, '//button[normalize-space()="START"]')) > 0
    if not has_stop:
        has_stop = len(driver.find_elements(By.XPATH,  '//button[normalize-space()="STOP"]'))  > 0
    return has_start, has_stop

def click_action(driver, action: str) -> Dict[str, Any]:
    info = {"clicked": False, "via": None, "selector": None}
    if action not in ("start", "stop"): return info
    css = f'button[data-action="{action}"]'
    info["selector"] = {"by": "css selector", "selector": css}
    try:
        btn = WebDriverWait(driver, 15).until(EC.element_to_be_clickable((By.CSS_SELECTOR, css)))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        time.sleep(0.2)
        btn.click()
        info["clicked"] = True
        info["via"] = "native"
        return info
    except Exception:
        try:
            btn = WebDriverWait(driver, 6).until(
                EC.element_to_be_clickable((By.XPATH, f'//button[normalize-space()="{action.upper()}"]'))
            )
            driver.execute_script("arguments[0].click();", btn)
            info["clicked"] = True
            info["via"] = "js"
        except Exception:
            pass
        return info

def collect_network(driver, limit=150):
    out = []
    try:
        for row in driver.get_log("performance"):
            msg = json.loads(row.get("message","{}")).get("message",{})
            method = msg.get("method","")
            params = msg.get("params",{})
            if method == "Network.requestWillBeSent":
                url = params.get("request",{}).get("url","")
                out.append(f"REQ {params.get('request',{}).get('method','GET')} {url}")
            elif method == "Network.responseReceived":
                url = params.get("response",{}).get("url","")
                status = params.get("response",{}).get("status",0)
                out.append(f"RES {status} {url}")
            if len(out) >= limit: break
    except Exception:
        pass
    return out

def check_status_with_requests(cookies: List[Dict[str, Any]]) -> int:
    try:
        s = requests.Session()
        for c in cookies:
            s.cookies.set(c["name"], c.get("value",""), domain=c.get("domain","magmanode.com"), path=c.get("path","/"))
        r = s.get(SERVER_URL, headers={"User-Agent": UA}, timeout=25, allow_redirects=True)
        return r.status_code
    except Exception:
        return -1

def run_diag(action: str) -> Dict[str, Any]:
    result = {
        "ok": True, "action": action or "", "server_url": SERVER_URL, "env_ok": bool(SERVER_URL),
        "cookies_count": 0, "cookies_error": None, "has_start": False, "has_stop": False,
        "status_before": "unknown", "status_after": "unknown", "selector": None, "note": "", "network": [],
    }
    cookies = _load_cookies_env()
    result["cookies_count"] = len(cookies)
    result["status_before"] = check_status_with_requests(cookies)

    if not SERVER_URL: return result

    drv = None
    try:
        drv = make_driver()
        inject_cookies(drv, cookies)
        drv.get(SERVER_URL)
        WebDriverWait(drv, 30).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        has_start, has_stop = buttons_present(drv)
        result["has_start"], result["has_stop"] = has_start, has_stop

        if action in ("start","stop") and (has_start or has_stop):
            info = click_action(drv, action)
            result["selector"] = info.get("selector")
            result["note"] = ("clicked=True via=%s" % info.get("via")) if info.get("clicked") else "click failed"
            time.sleep(2)  # اجازه به XHR

        result["network"] = collect_network(drv, limit=200)
        result["status_after"] = check_status_with_requests(cookies)

    except Exception as e:
        result["note"] = f"driver-error: {repr(e)}"
    finally:
        if drv:
            try: drv.quit()
            except Exception: pass
    return result

# ---------- Flask ----------
@app.route("/")
def home():
    return ("<h1>Render Diagnostic</h1>"
            "<p>رفتن به گزارش: <a href='/diag'>/diag</a></p>"
            "<p>JSON: <a href='/diag?format=json'>/diag?format=json</a></p>"
            "<p>کلیک و گزارش همزمان: <a href='/diag?action=start'>start</a> | "
            "<a href='/diag?action=stop'>stop</a></p>"
            "<p>هویت مرورگر: <a href='/whoami'>/whoami</a></p>")

@app.route("/diag")
def diag():
    action = request.args.get("action","").strip().lower()
    want_json = request.args.get("format","").lower()=="json"
    try:
        result = run_diag(action)
    except Exception:
        result = {"ok": False, "error": traceback.format_exc()}
    if want_json:
        return Response(json.dumps(result, ensure_ascii=False), mimetype="application/json")

    lines = [
        "Diag",
        f"ok: {result.get('ok')}", "",
        f"action: {result.get('action')}",
        f"server_url: {result.get('server_url')}",
        f"cookies_count: {result.get('cookies_count')}",
        f"has_start: {result.get('has_start')} | has_stop: {result.get('has_stop')}",
        f"status_before: {result.get('status_before')}",
        f"status_after: {result.get('status_after')}",
        f"selector: {result.get('selector')}",
        f"note: {result.get('note')}",
    ]
    if result.get("network"):
        lines.append("network:")
        for ln in result["network"][:50]:
            lines.append(f"- {ln}")
    lines.append("")
    lines.append("JSON | /api/status")
    return Response("\n".join(lines), mimetype="text/plain; charset=utf-8")

@app.route("/whoami")
def whoami():
    info = {"env_ok": bool(SERVER_URL), "cookies_count": len(_load_cookies_env()), "ua": UA}
    return Response(json.dumps(info, ensure_ascii=False), mimetype="application/json")

@app.route("/api/status")
def api_status():
    out = {"service":"render_diag","version":"v3","ok":True,
           "pages":{"self":{"url":request.host_url,"status":200,"ok":True},
                    "magma":{"url":SERVER_URL,"status":-1,"ok":False}}}
    try:
        r = requests.get(SERVER_URL, headers={"User-Agent": UA}, timeout=20, allow_redirects=True)
        out["pages"]["magma"]["status"] = r.status_code
        out["pages"]["magma"]["ok"] = (r.status_code > 0 and r.status_code < 500)
    except Exception:
        pass
    return Response(json.dumps(out, ensure_ascii=False), mimetype="application/json")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","10000")), debug=False)
