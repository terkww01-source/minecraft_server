# -*- coding: utf-8 -*-
import os, json, time, traceback, urllib.parse
from typing import Any, Dict, List

import requests
from flask import Flask, request, Response

# ضد‌بات Cloudflare → مرورگر واقعی‌نما
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

SERVER_URL = os.getenv("MAGMANODE_SERVER_URL", "").strip()
COOKIES_RAW = os.getenv("MAGMANODE_COOKIES_JSON", "").strip()
UA = os.getenv(
    "MAGMANODE_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)
CHROME_BIN = "/usr/bin/chromium"

app = Flask("render_diag")

# ---------------- helpers ----------------
def _load_cookies() -> List[Dict[str, Any]]:
    if not COOKIES_RAW:
        return []
    try:
        data = json.loads(COOKIES_RAW)
        if isinstance(data, dict):
            data = [data]
        out = []
        for c in data:
            d = {
                "name": c["name"],
                "value": c.get("value", ""),
                "domain": c.get("domain", "magmanode.com"),
                "path": c.get("path", "/"),
                "secure": bool(c.get("secure", True)),
                "httpOnly": bool(c.get("httpOnly", False)),
            }
            out.append(d)
        return out
    except Exception:
        return []

def _get_service_id() -> str:
    try:
        q = urllib.parse.urlparse(SERVER_URL).query
        return dict(urllib.parse.parse_qsl(q)).get("id", "")
    except Exception:
        return ""

def make_driver():
    opts = uc.ChromeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1366,768")
    opts.add_argument("--lang=en-US")
    opts.add_argument(f"--user-agent={UA}")
    # برای گرفتن لاگ‌ها لازم نیست، ولی بد نیست
    opts.set_capability("goog:loggingPrefs", {"browser":"ALL"})
    drv = uc.Chrome(options=opts, browser_executable_path=CHROME_BIN, use_subprocess=True)
    drv.set_page_load_timeout(60)
    return drv

def inject_cookies(drv, cookies):
    drv.get("https://magmanode.com/")
    time.sleep(0.8)
    for c in cookies:
        try:
            drv.add_cookie(c)
        except Exception:
            pass

def buttons_present(drv):
    has_start = len(drv.find_elements(By.CSS_SELECTOR, 'button[data-action="start"]')) > 0
    has_stop  = len(drv.find_elements(By.CSS_SELECTOR, 'button[data-action="stop"]')) > 0
    if not has_start:
        has_start = len(drv.find_elements(By.XPATH, '//button[normalize-space()="START"]')) > 0
    if not has_stop:
        has_stop = len(drv.find_elements(By.XPATH, '//button[normalize-space()="STOP"]')) > 0
    return has_start, has_stop

def power_via_fetch(drv, action: str, sid: str) -> Dict[str, Any]:
    """از داخل همان صفحه درخواست /power بده (Cloudflare گذر می‌کند)."""
    url = f"https://magmanode.com/power?id={sid}&action={action}"
    js = """
      const url = arguments[0], done = arguments[1];
      fetch(url, {method:'GET', credentials:'include'})
        .then(r => r.text().then(t => done({status:r.status, ok:r.ok, body:t.slice(0,200)})))
        .catch(e => done({status:-1, ok:false, error:String(e)}));
    """
    try:
        return drv.execute_async_script(js, url)
    except Exception as e:
        return {"status": -1, "ok": False, "error": repr(e)}

def click_button(drv, action: str) -> Dict[str, Any]:
    info = {"clicked": False, "via": None, "selector": f'button[data-action="{action}"]'}
    try:
        btn = WebDriverWait(drv, 12).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, f'button[data-action="{action}"]'))
        )
        drv.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        time.sleep(0.15)
        btn.click()
        info.update(clicked=True, via="native")
    except Exception:
        try:
            btn = WebDriverWait(drv, 5).until(
                EC.element_to_be_clickable((By.XPATH, f'//button[normalize-space()="{action.upper()}"]'))
            )
            drv.execute_script("arguments[0].click();", btn)
            info.update(clicked=True, via="js")
        except Exception:
            pass
    return info

def run_diag(action: str) -> Dict[str, Any]:
    result = {
        "ok": True, "action": action or "", "server_url": SERVER_URL, "env_ok": bool(SERVER_URL),
        "cookies_count": 0, "has_start": False, "has_stop": False,
        "status_before": -1, "status_after": -1,
        "selector": None, "note": "", "network": []
    }
    cookies = _load_cookies()
    result["cookies_count"] = len(cookies)
    if not SERVER_URL:
        return result

    sid = _get_service_id()
    drv = None
    try:
        # 1) مرورگر
        drv = make_driver()
        inject_cookies(drv, cookies)

        # 2) رفتن به صفحه سرویس
        drv.get(SERVER_URL)
        WebDriverWait(drv, 30).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        result["has_start"], result["has_stop"] = buttons_present(drv)

        # 3) اول مستقیم /power را از داخل صفحه صدا بزن
        power_res = {}
        if action in ("start","stop"):
            power_res = power_via_fetch(drv, action, sid)
            result["note"] = f"power_fetch: {power_res}"

            # اگر به هر دلیل ok نبود، یک کلیک هم بکن
            if not power_res.get("ok"):
                info = click_button(drv, action)
                result["selector"] = {"by": "css selector", "selector": info["selector"]}
                result["note"] += f" | click: {info}"

        # 4) وضعیت بعدی را با requests فقط تست می‌کنیم (ممکن است 403 بدهد، مهم نیست)
        try:
            s = requests.Session()
            for c in cookies:
                s.cookies.set(c["name"], c["value"], domain=c.get("domain","magmanode.com"), path=c.get("path","/"))
            r = s.get(SERVER_URL, headers={"User-Agent": UA}, timeout=20, allow_redirects=True)
            result["status_after"] = r.status_code
        except Exception:
            result["status_after"] = -1

    except Exception as e:
        result["ok"] = True  # گزارش بده ولی سرویس بالا باشد
        result["note"] = f"driver-error: {repr(e)}\n{traceback.format_exc()}"
    finally:
        if drv:
            try: drv.quit()
            except: pass
    return result

# ---------------- Flask ----------------
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
    as_json = request.args.get("format","").lower() == "json"
    res = run_diag(action)
    if as_json:
        return Response(json.dumps(res, ensure_ascii=False), mimetype="application/json")
    lines = [
        "Diag",
        f"ok: {res['ok']}", "",
        f"action: {res['action']}",
        f"server_url: {res['server_url']}",
        f"cookies_count: {res['cookies_count']}",
        f"has_start: {res['has_start']} | has_stop: {res['has_stop']}",
        f"status_before: {res.get('status_before', 'unknown')}",
        f"status_after: {res.get('status_after', 'unknown')}",
        f"selector: {res.get('selector')}",
        f"note: {res.get('note')}",
        "JSON | /api/status"
    ]
    return Response("\n\n".join(lines), mimetype="text/plain; charset=utf-8")

@app.route("/whoami")
def whoami():
    return Response(json.dumps({"env_ok": bool(SERVER_URL),
                                "cookies_count": len(_load_cookies()),
                                "ua": UA}, ensure_ascii=False),
                    mimetype="application/json")

@app.route("/api/status")
def api_status():
    out = {"service":"render_diag","version":"v4","ok":True}
    return Response(json.dumps(out, ensure_ascii=False), mimetype="application/json")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","10000")), debug=False)
