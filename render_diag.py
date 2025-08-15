import os, json, time, traceback
from flask import Flask, request, jsonify, Response
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

app = Flask(__name__)

# ---------- helpers ----------
def get_server_url() -> str:
    return (os.getenv("MAGMANODE_SERVER_URL") or "").strip()

def get_cookie_env() -> list:
    raw = (os.getenv("MAGMANODE_COOKIES_JSON") or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        # فیلتر کلیدهای غیرمجاز برای Selenium
        filtered = []
        for c in data:
            item = {
                "name": c.get("name"),
                "value": c.get("value"),
                "domain": c.get("domain") or "magmanode.com",
                "path": c.get("path") or "/",
                "secure": bool(c.get("secure", False)),
                "httpOnly": bool(c.get("httpOnly", False)),
            }
            # sameSite/hostOnly/storeId/session/id رو اضافه نکن
            if item["name"] and item["value"]:
                filtered.append(item)
        return filtered
    except Exception:
        return []

def make_driver():
    chrome_bin = os.getenv("CHROME_BIN", "/usr/bin/chromium")
    chrome_drv = os.getenv("CHROME_DRIVER", "/usr/bin/chromedriver")

    opts = Options()
    opts.binary_location = chrome_bin
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1366,768")
    # اگر سایت فونت/رسانه می‌خواد:
    opts.add_argument("--autoplay-policy=no-user-gesture-required")

    service = Service(chrome_drv)  # ✅ جایگزین executable_path
    drv = webdriver.Chrome(service=service, options=opts)
    drv.set_page_load_timeout(40)
    return drv

def inject_cookies(drv, cookies: list) -> int:
    if not cookies:
        return 0
    # باید روی همان دامنه باشیم تا کوکی اضافه شود
    drv.get("https://magmanode.com/")
    for ck in cookies:
        try:
            drv.add_cookie(ck)
        except Exception:
            pass
    return len(cookies)

def click_btn(drv, server_url: str, action: str):
    # صفحه را باز کن
    drv.get(server_url)
    wait = WebDriverWait(drv, 15)

    # دکمه‌ها با data-action مشخص هستند
    sel = f"button[data-action='{action}']"
    btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, sel)))
    # اسکرول به دکمه برای اطمینان
    drv.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
    time.sleep(0.2)
    btn.click()
    time.sleep(1.0)

    # چک وجود هر دو دکمه برای گزارش
    has_start = len(drv.find_elements(By.CSS_SELECTOR, "button[data-action='start']")) > 0
    has_stop  = len(drv.find_elements(By.CSS_SELECTOR, "button[data-action='stop']")) > 0
    return {"has_start": has_start, "has_stop": has_stop}

def probe_page(drv, server_url: str):
    try:
        drv.get(server_url)
        has_start = len(drv.find_elements(By.CSS_SELECTOR, "button[data-action='start']")) > 0
        has_stop  = len(drv.find_elements(By.CSS_SELECTOR, "button[data-action='stop']")) > 0
        return True, has_start, has_stop
    except Exception:
        return False, False, False

# ---------- routes ----------
@app.route("/")
def home():
    return (
        "Render Diagnostic<br>"
        "رفتن به گزارش: /diag<br><br>"
        "JSON: /diag?format=json<br><br>"
        "کلیک و گزارش همزمان: "
        "<a href='/diag?action=start'>start</a> | "
        "<a href='/diag?action=stop'>stop</a>"
    )

@app.route("/api/status")
def api_status():
    srv = get_server_url()
    ok_self = True
    return jsonify({
        "ok": True,
        "service": "render_diag",
        "version": "v2",
        "pages": {
            "self": {"ok": ok_self, "status": 200, "url": request.host_url},
            "magma": {"ok": bool(srv), "status": 200 if srv else 500, "url": srv or ""},
        }
    })

@app.route("/diag")
def diag():
    fmt = request.args.get("format", "").lower()
    action = (request.args.get("action") or "").strip().lower()
    server_url = get_server_url()
    cookies = get_cookie_env()

    payload = {
        "ok": False,
        "action": action,
        "server_url": server_url,
        "env_ok": bool(server_url),
        "cookies_count": len(cookies),
        "has_start": False,
        "has_stop": False,
        "error": None,
        "trace": None,
    }

    try:
        drv = make_driver()
        try:
            inject_cookies(drv, cookies)
            if action in ("start", "stop"):
                step = click_btn(drv, server_url, action)
                payload.update(step)
            else:
                okp, hs, hp = probe_page(drv, server_url)
                payload["has_start"] = hs
                payload["has_stop"] = hp
            payload["ok"] = True
        finally:
            try:
                drv.quit()
            except Exception:
                pass
    except Exception as e:
        payload["ok"] = False
        payload["error"] = repr(e)
        payload["trace"] = traceback.format_exc()

    if fmt == "json":
        return jsonify(payload)
    # ساده و کوتاه
    html = [
        f"Diag<br>ok: {payload['ok']}<br><br>",
        f"action: {payload['action']}<br>",
        f"server_url: {payload['server_url']}<br>",
        f"cookies_count: {payload['cookies_count']}<br>",
        f"has_start: {payload['has_start']} | has_stop: {payload['has_stop']}<br>",
    ]
    if payload["error"]:
        html.append(f"<br><b>error:</b> {payload['error']}")
    return Response("".join(html), mimetype="text/html")
# ----------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
