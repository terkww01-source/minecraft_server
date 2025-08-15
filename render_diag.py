# render_diag.py
# -*- coding: utf-8 -*-
"""
خلاصهٔ کار:
- داخل Render یک Flask سرور بالا می‌آورد که /diag را برای گزارش عیب‌یابی ارائه می‌کند.
- با Selenium (Headless Chromium) وارد پنل می‌شود، کوکی/دو-لینک را امتحان می‌کند،
  دکمه‌های START/STOP را دقیقاً با selector معتبر شما کلیک می‌کند،
  اسکرین‌شات قبل/بعد می‌گیرد، لاگ کنسول و شبکه را جمع می‌کند،
  و گزارش JSON/HTML می‌دهد تا بفهمیم واقعاً چه اتفاقی افتاده.
"""

import os, json, time, base64, pathlib, traceback
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, Response
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

APP = Flask(__name__, static_folder="static", static_url_path="/static")
ROOT = pathlib.Path(__file__).parent.resolve()
SHOT_DIR = ROOT / "static" / "shots"
SHOT_DIR.mkdir(parents=True, exist_ok=True)

# --------- تنظیمات از طریق ENV ----------
PANEL_URL1 = os.getenv("PANEL_URL1", "https://magmanode.com/server?id=770999")
PANEL_URL2 = os.getenv("PANEL_URL2", PANEL_URL1)
COOKIES_JSON_INLINE = os.getenv("COOKIES_JSON", "")  # اگر خواستی JSON کامل کوکی را در همین ENV بریزی
COOKIES_PATH = os.getenv("COOKIES_PATH", "")         # یا مسیر فایل کوکی داخل ایمیج/کنتینر
HEADLESS = os.getenv("HEADLESS", "true").lower() != "false"
WINDOW = os.getenv("WINDOW", "1280,2200")
CLICK_TIMEOUT = int(os.getenv("CLICK_TIMEOUT_SEC", "15"))
PAGE_TIMEOUT = int(os.getenv("PAGE_TIMEOUT_SEC", "20"))

# ---------- ابزارها ----------
def _now():
    return datetime.utcnow().strftime("%Y%m%d-%H%M%S")

def _chrome_binary_guess():
    for p in ["/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]:
        if os.path.exists(p):
            return p
    return None

def make_driver():
    opts = Options()
    if HEADLESS:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=" + WINDOW)
    opts.add_argument("--lang=en-US")
    # لاگ کنسول + Network (performance)
    opts.set_capability("goog:loggingPrefs", {"browser": "ALL", "performance": "ALL"})
    bin_path = _chrome_binary_guess()
    if bin_path:
        opts.binary_location = bin_path
    svc_path = "/usr/bin/chromedriver"  # در Dockerfile شما نصب شده
    service = Service(executable_path=svc_path)
    driver = webdriver.Chrome(service=service, options=opts)
    try:
        driver.execute_cdp_cmd("Network.enable", {})
    except Exception:
        pass
    return driver

def inject_cookies(driver, url):
    # اول صفحه را باز کن تا دامنه درست شود
    driver.get(url)
    cookies = []
    try:
        if COOKIES_JSON_INLINE.strip():
            cookies = json.loads(COOKIES_JSON_INLINE)
        elif COOKIES_PATH and os.path.exists(COOKIES_PATH):
            with open(COOKIES_PATH, "r", encoding="utf-8") as f:
                cookies = json.load(f)
    except Exception as e:
        print("⚠️ مشکل خواندن کوکی:", e)

    ok = 0
    for c in cookies:
        try:
            # حذف فیلدهای ناسازگار
            for k in ["sameSite", "storeId", "hostOnly"]:
                if k in c:
                    c.pop(k, None)
            driver.add_cookie(c)
            ok += 1
        except Exception:
            pass
    if ok:
        driver.get(url)  # refresh با کوکی
    return ok

def shot(driver, name):
    path = SHOT_DIR / f"{_now()}_{name}.png"
    driver.save_screenshot(str(path))
    return f"/static/shots/{path.name}"

def get_visible_texts(driver, selectors):
    found = {}
    for sel in selectors:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            txt = el.text.strip()
            if txt:
                found[sel] = txt
        except Exception:
            pass
    return found

def get_console_logs(driver):
    out = []
    try:
        for entry in driver.get_log("browser"):
            out.append({"level": entry.get("level"), "message": entry.get("message")})
    except Exception:
        pass
    return out

def get_network_hits(driver):
    # از performance logs برای یافتن درخواست‌های start/stop استفاده می‌کنیم
    hits = []
    try:
        for ent in driver.get_log("performance"):
            try:
                msg = json.loads(ent["message"])["message"]
                if msg.get("method") == "Network.requestWillBeSent":
                    req = msg.get("params", {}).get("request", {})
                    url = req.get("url", "")
                    method = req.get("method", "")
                    if any(k in url.lower() for k in ["start", "stop", "power", "server"]):
                        hits.append({"method": method, "url": url})
            except Exception:
                continue
    except Exception:
        pass
    return hits

def wait_for(driver, css, timeout=PAGE_TIMEOUT):
    return WebDriverWait(driver, timeout).until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, css))
    )

def click_precisely(driver, action):
    css = 'button[data-action="start"]' if action == "start" else 'button[data-action="stop"]'
    el = wait_for(driver, css, timeout=CLICK_TIMEOUT)
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    # چند روش کلیک برای مطمئن شدن
    try:
        el.click()
    except Exception:
        driver.execute_script("arguments[0].click();", el)
    return css

def open_best_url(driver):
    # لینک اول → اگر دکمه‌ها پیدا نشدند، لینک دوم
    tried = []
    for url in [PANEL_URL1, PANEL_URL2]:
        if not url: 
            continue
        tried.append(url)
        inject_cookies(driver, url)
        try:
            wait_for(driver, 'button[data-action="start"]', timeout=8)
            return url, True, tried
        except Exception:
            continue
    # اگر هیچ‌کدام آماده نبودند
    return (PANEL_URL1 or PANEL_URL2), False, tried

def collect_status(driver):
    # هر چیزی که شبیه وضعیت باشد را برمی‌داریم تا ببینیم offline/starting/online داریم یا نه
    selectors = [
        ".status", ".status-badge", ".badge", "#status", "[data-status]", ".text-green-600",
        ".text-red-600", ".text-yellow-600", ".server-status", ".state", ".chip", ".label"
    ]
    return get_visible_texts(driver, selectors)

def diagnose_once(do_action=None):
    # do_action: None | "start" | "stop"
    driver = make_driver()
    report = {
        "ok": False,
        "narrative": [],
        "used_url": None,
        "tried_urls": [],
        "before": {},
        "after": {},
        "clicked": None,
        "click_selector": None,
        "errors": [],
        "shots": {}
    }
    try:
        url, ready, tried = open_best_url(driver)
        report["used_url"] = driver.current_url or url
        report["tried_urls"] = tried
        if not ready:
            report["shots"]["page_not_ready"] = shot(driver, "page_not_ready")
            report["narrative"].append("صفحه آماده نبود (دکمه START دیده نشد).")
            return report

        report["shots"]["before"] = shot(driver, "before")
        report["before"]["title"] = driver.title
        report["before"]["status_like"] = collect_status(driver)
        report["before"]["start_visible"] = bool(len(driver.find_elements(By.CSS_SELECTOR, 'button[data-action="start"]')))
        report["before"]["stop_visible"]  = bool(len(driver.find_elements(By.CSS_SELECTOR, 'button[data-action="stop"]')))
        report["before"]["console"] = get_console_logs(driver)
        report["before"]["network_hits"] = get_network_hits(driver)

        if do_action in ("start", "stop"):
            report["narrative"].append(f"تلاش برای کلیک روی {do_action.upper()}.")
            css = click_precisely(driver, do_action)
            report["clicked"] = True
            report["click_selector"] = css
            # کمی صبر برای تغییرات UI/درخواست شبکه
            time.sleep(3)

        report["shots"]["after"] = shot(driver, "after")
        report["after"]["title"] = driver.title
        report["after"]["status_like"] = collect_status(driver)
        report["after"]["start_visible"] = bool(len(driver.find_elements(By.CSS_SELECTOR, 'button[data-action="start"]')))
        report["after"]["stop_visible"]  = bool(len(driver.find_elements(By.CSS_SELECTOR, 'button[data-action="stop"]')))
        report["after"]["console"] = get_console_logs(driver)
        report["after"]["network_hits"] = get_network_hits(driver)

        report["ok"] = True
        return report
    except Exception as e:
        report["errors"].append(str(e))
        report["errors"].append(traceback.format_exc())
        return report
    finally:
        try:
            driver.quit()
        except Exception:
            pass

# ----------- Flask Routes -----------
@APP.get("/")
def root():
    return (
        "<h2>Render Diagnostic</h2>"
        "<p>رفتن به گزارش: <a href='/diag'>/diag</a></p>"
        "<p>JSON: <a href='/diag?format=json'>/diag?format=json</a></p>"
        "<p>کلیک و گزارش همزمان: "
        "<a href='/diag?action=start'>start</a> | "
        "<a href='/diag?action=stop'>stop</a></p>"
    )

@APP.get("/diag")
def diag():
    action = request.args.get("action")  # None | start | stop
    fmt = request.args.get("format", "html")
    rep = diagnose_once(do_action=action)

    if fmt == "json":
        return jsonify(rep)

    # HTML خلاصه‌شده و خوانا
    def pre(js):
        return f"<pre style='white-space:pre-wrap;background:#111;color:#ddd;padding:12px;border-radius:8px'>{js}</pre>"

    html = [
        "<h2>گزارش عیب‌یابی</h2>",
        f"<p><b>used_url:</b> {rep.get('used_url')}</p>",
        f"<p><b>tried_urls:</b> {', '.join(rep.get('tried_urls', []))}</p>",
        f"<p><b>clicked:</b> {rep.get('clicked')} <small>selector: {rep.get('click_selector')}</small></p>",
        "<h3>اسکرین‌شات‌ها</h3>"
    ]
    for k, v in rep.get("shots", {}).items():
        html.append(f"<div><b>{k}</b><br><img src='{v}' style='max-width: 520px; border:1px solid #444; border-radius:8px;'/></div><br>")

    html.append("<h3>قبل از کلیک</h3>")
    html.append(pre(json.dumps(rep.get("before", {}), ensure_ascii=False, indent=2)))
    html.append("<h3>بعد از کلیک</h3>")
    html.append(pre(json.dumps(rep.get("after", {}), ensure_ascii=False, indent=2)))
    if rep.get("errors"):
        html.append("<h3 style='color:#f55'>Errors</h3>")
        html.append(pre(json.dumps(rep["errors"], ensure_ascii=False, indent=2)))

    return Response("\n".join(html), mimetype="text/html; charset=utf-8")

# فایل‌های اسکرین‌شات از /static/shots سرو می‌شوند (Flask static)

if __name__ == "__main__":
    # روی Render معمولاً PORT در ENV ست می‌شود
    port = int(os.getenv("PORT", "10000"))
    APP.run(host="0.0.0.0", port=port, debug=False)
