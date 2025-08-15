import os, json, time, threading, urllib.request, urllib.error
from flask import Flask, request, jsonify, Response
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

APP_PORT = int(os.getenv("PORT", "10000"))
SERVER_URL = (os.getenv("MAGMANODE_SERVER_URL", "") or "").strip()
COOKIES_JSON = os.getenv("MAGMANODE_COOKIES_JSON", "")

app = Flask(__name__)

# ---------- utils ----------
def head_status(url, timeout=8):
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0

def make_driver():
    # selenium 4 – بدون executable_path
    chrome_bin = "/usr/bin/chromium"
    chrome_drv = "/usr/bin/chromedriver"

    opts = webdriver.ChromeOptions()
    opts.binary_location = chrome_bin
    # headless پایدار
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1366,768")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36")
    # لاگ شبکه برای دیباگ
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL", "browser": "ALL"})

    service = Service(chrome_drv)
    drv = webdriver.Chrome(service=service, options=opts)
    try:
        # فعال‌کردن Network برای جمع‌آوری لاگ
        drv.execute_cdp_cmd("Network.enable", {})
    except Exception:
        pass
    return drv

def inject_cookies(driver):
    if not COOKIES_JSON.strip():
        return 0, None
    try:
        cookies = json.loads(COOKIES_JSON)
        # باید اول وارد دامنه شویم تا اجازهٔ setCookie داشته باشیم
        driver.get("https://magmanode.com/")
        time.sleep(0.5)
        ok = 0
        for c in cookies:
            # حداقل فیلدهای لازم
            cookie = {
                "name": c["name"],
                "value": c["value"],
                "domain": "magmanode.com",
                "path": "/",
                "secure": bool(c.get("secure", True)),
                "httpOnly": bool(c.get("httpOnly", True)),
            }
            try:
                driver.add_cookie(cookie)
                ok += 1
            except Exception:
                pass
        return ok, None
    except Exception as e:
        return 0, str(e)

def find_btns(driver):
    try:
        start_btn = WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'button[data-action="start"]'))
        )
    except Exception:
        start_btn = None
    try:
        stop_btn = WebDriverWait(driver, 3).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'button[data-action="stop"]'))
        )
    except Exception:
        stop_btn = None
    return start_btn, stop_btn

def smart_click(driver, el):
    if el is None:
        return False, "no_element"
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        WebDriverWait(driver, 5).until(EC.element_to_be_clickable(el))
    except Exception:
        pass
    # 3 روش کلیک
    for how in ("native", "js", "events"):
        try:
            if how == "native":
                el.click()
            elif how == "js":
                driver.execute_script("arguments[0].click();", el)
            else:
                driver.execute_script("""
                  const e1=new PointerEvent('pointerdown',{bubbles:true});
                  const e2=new MouseEvent('mousedown',{bubbles:true});
                  const e3=new PointerEvent('pointerup',{bubbles:true});
                  const e4=new MouseEvent('mouseup',{bubbles:true});
                  const e5=new MouseEvent('click',{bubbles:true});
                  arguments[0].dispatchEvent(e1);
                  arguments[0].dispatchEvent(e2);
                  arguments[0].dispatchEvent(e3);
                  arguments[0].dispatchEvent(e4);
                  arguments[0].dispatchEvent(e5);
                """, el)
            return True, how
        except Exception as e:
            last_err = str(e)
    return False, last_err

def collect_net(driver):
    out = []
    try:
        logs = driver.get_log("performance")
        for entry in logs:
            try:
                msg = json.loads(entry["message"])["message"]
                m = msg.get("method")
                p = msg.get("params", {})
                if m == "Network.requestWillBeSent":
                    req = p.get("request", {})
                    out.append(f'REQ {req.get("method","")} {req.get("url","")}')
                elif m == "Network.responseReceived":
                    resp = p.get("response", {})
                    out.append(f'RES {resp.get("status",0)} {resp.get("url","")}')
            except Exception:
                pass
    except Exception:
        pass
    return out[-200:]  # فقط آخرین‌ها

def run_diag(action=""):
    info = {
        "ok": False,
        "action": action,
        "server_url": SERVER_URL,
        "env_ok": bool(SERVER_URL),
        "cookies_count": 0,
        "cookies_error": None,
        "has_start": False,
        "has_stop": False,
        "status_before": "unknown",
        "status_after": "unknown",
        "selector": None,
        "network": [],
        "note": "",
    }
    if not SERVER_URL:
        info["note"] = "SERVER_URL missing"
        return info

    drv = make_driver()
    try:
        # کوکی‌ها
        cnt, err = inject_cookies(drv)
        info["cookies_count"] = cnt
        info["cookies_error"] = err

        # وضعیت قبل (HEAD)
        info["status_before"] = head_status(SERVER_URL)

        # رفتن به صفحهٔ سرور
        drv.get(SERVER_URL)
        time.sleep(1.2)

        start_btn, stop_btn = find_btns(drv)
        info["has_start"] = start_btn is not None
        info["has_stop"]  = stop_btn is not None

        if action in ("start", "stop"):
            target = start_btn if action == "start" else stop_btn
            info["selector"] = {"by": "css selector", "selector": f'button[data-action="{action}"]'}
            clicked, how = smart_click(drv, target)
            info["note"] = f"clicked={clicked} via={how}"
            # کمی صبر و جمع‌آوری لاگ شبکه
            t0 = time.time()
            while time.time() - t0 < 6:
                time.sleep(0.5)
                info["network"].extend(collect_net(drv))

        # وضعیت بعد (HEAD)
        info["status_after"] = head_status(SERVER_URL)
        info["ok"] = True
        return info
    finally:
        try:
            drv.quit()
        except Exception:
            pass

# ---------- routes ----------
@app.get("/")
def index():
    return """<h1>Render Diagnostic</h1>
<p>رفتن به گزارش: <a href="/diag">/diag</a></p>
<p>JSON: <a href="/diag?format=json">/diag?format=json</a></p>
<p>کلیک و گزارش همزمان: <a href="/diag?action=start">start</a> | <a href="/diag?action=stop">stop</a></p>
<p>هویت مرورگر: <a href="/whoami">/whoami</a></p>
"""

@app.get("/whoami")
def whoami():
    ua = request.headers.get("User-Agent", "")
    return jsonify({
        "ua": ua,
        "env_ok": bool(SERVER_URL),
        "cookies_count": len(json.loads(COOKIES_JSON)) if COOKIES_JSON.strip() else 0
    })

@app.get("/api/status")
def api_status():
    self_url = request.url_root
    magma = {"url": SERVER_URL, "status": head_status(SERVER_URL), "ok": False}
    magma["ok"] = magma["status"] in (200, 302, 304)
    return jsonify({
        "service": "render_diag",
        "version": "v2",
        "ok": True,
        "pages": {
            "self": {"url": self_url, "status": 200, "ok": True},
            "magma": magma
        }
    })

@app.get("/diag")
def diag():
    action = request.args.get("action", "").strip().lower()
    fmt = request.args.get("format", "")
    info = run_diag(action)
    if fmt == "json":
        return jsonify(info)

    # human-readable
    lines = [
        f"Diag",
        f"ok: {info['ok']}",
        "",
        f"action: {info['action']}",
        f"server_url: {info['server_url']}",
        f"cookies_count: {info['cookies_count']}",
        f"has_start: {info['has_start']} | has_stop: {info['has_stop']}",
        f"status_before: {info['status_before']}",
        f"status_after: {info['status_after']}",
        f"selector: {info['selector']}",
        f"note: {info['note']}",
        "network:"
    ] + [f"- {n}" for n in info["network"][-30:]]
    return Response("\n".join(lines), mimetype="text/plain; charset=utf-8")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=APP_PORT, debug=False)
