import os, json, time, threading
from contextlib import contextmanager
from flask import Flask, request, jsonify, Response, redirect

try:
    import requests
except Exception:
    requests = None

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

APP = Flask("render_diag")

# ===== تنظیمات =====
SERVER_URL = (os.getenv("MAGMANODE_SERVER_URL", "").strip() or "")
UA = os.getenv("MAGMANODE_USER_AGENT",
               "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36").strip()
PROXY_URL = os.getenv("PROXY_URL", "").strip()
COOKIE_FILE = os.getenv("MAGMANODE_COOKIES_FILE", "/tmp/magma_cookies.json")
ENV_COOKIES = os.getenv("MAGMANODE_COOKIES_JSON", "").strip()
CHROME_BIN = "/usr/bin/chromium"
CHROME_DRV = "/usr/bin/chromedriver"

# وضعیت زمان‌بندی
_schedule = {"at": None, "action": None, "armed": False}
_last_run = {"when": None, "result": None}
_stop_keepalive = threading.Event()

# ===== ابزار =====
def _load_cookies():
    # اول از فایل، بعد از ENV
    if os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass
    try:
        return json.loads(ENV_COOKIES) if ENV_COOKIES else []
    except Exception:
        return []

def _to_selenium_cookie(c):
    return {
        "name": c.get("name"),
        "value": c.get("value"),
        "domain": c.get("domain") or "magmanode.com",
        "path": c.get("path") or "/",
        "secure": bool(c.get("secure", True)),
        "httpOnly": bool(c.get("httpOnly", True)),
    }

def _read_perf_log(driver):
    out = []
    try:
        for entry in driver.get_log("performance"):
            msg = json.loads(entry["message"])["message"]
            method = msg.get("method", "")
            params = msg.get("params", {})
            if method == "Network.requestWillBeSent":
                req = params.get("request", {})
                out.append(f"- REQ {req.get('method','GET')} {req.get('url','')}")
            elif method == "Network.responseReceived":
                res = params.get("response", {})
                out.append(f"- RES {int(res.get('status', -1))} {res.get('url','')}")
    except Exception:
        pass
    return out

@contextmanager
def make_driver():
    opts = Options()
    opts.binary_location = CHROME_BIN
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1366,768")
    opts.add_argument(f"--user-agent={UA}")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    if PROXY_URL:
        opts.add_argument(f"--proxy-server={PROXY_URL}")

    service = Service(CHROME_DRV)
    driver = webdriver.Chrome(service=service, options=opts)

    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": 'Object.defineProperty(navigator,"webdriver",{get:()=>undefined});'}
        )
    except Exception:
        pass

    try:
        yield driver
    finally:
        try:
            driver.quit()
        except Exception:
            pass

def inject_cookies(driver, cookies):
    if not cookies:
        return 0, None
    driver.get("https://magmanode.com/")
    time.sleep(0.3)
    added, err = 0, None
    for c in cookies:
        try:
            driver.add_cookie(_to_selenium_cookie(c))
            added += 1
        except Exception as e:
            err = str(e)
    return added, err

def ensure_consent(driver):
    # تلاش برای بستن پنجره‌های consent/ads
    try:
        time.sleep(0.3)
        for iframe in driver.find_elements(By.TAG_NAME, "iframe"):
            try:
                src = iframe.get_attribute("src") or ""
                if "fundingchoicesmessages.google.com" in src:
                    driver.switch_to.frame(iframe)
                    for b in driver.find_elements(By.XPATH, "//button|//div[@role='button']"):
                        txt = (b.text or "").strip().lower()
                        if any(k in txt for k in ["accept", "agree", "continue", "allow", "ok"]):
                            try:
                                b.click()
                                time.sleep(0.2)
                            except Exception:
                                pass
                    driver.switch_to.default_content()
            except Exception:
                driver.switch_to.default_content()
    except Exception:
        pass

def click_once(action):
    """یک بار اجرا: صفحه رو باز می‌کنه، کوکی می‌ذاره، کلیک می‌کنه، لاگ برمی‌گردونه."""
    info = {
        "ok": True,
        "action": action,
        "server_url": SERVER_URL,
        "cookies_count": 0,
        "cookies_error": None,
        "has_start": False,
        "has_stop": False,
        "selector": None,
        "note": "",
        "network": [],
    }
    if not SERVER_URL:
        info["ok"] = False
        info["note"] = "SERVER_URL empty"
        return info

    cookies = _load_cookies()
    with make_driver() as driver:
        cnt, err = inject_cookies(driver, cookies)
        info["cookies_count"] = cnt
        info["cookies_error"] = err

        driver.get(SERVER_URL)
        time.sleep(0.8)
        ensure_consent(driver)

        try:
            info["has_start"] = len(driver.find_elements(By.CSS_SELECTOR, 'button[data-action="start"]')) > 0
            info["has_stop"] = len(driver.find_elements(By.CSS_SELECTOR, 'button[data-action="stop"]')) > 0
        except Exception:
            pass

        info["network"].extend(_read_perf_log(driver))

        if action in ("start", "stop"):
            selector = f'button[data-action="{action}"]'
            clicked, via = False, "native"
            try:
                btn = WebDriverWait(driver, 12).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                )
                try:
                    btn.click()
                    clicked = True
                except Exception:
                    driver.execute_script("arguments[0].click();", btn)
                    clicked, via = True, "js"
            except Exception:
                pass
            info["selector"] = {"by": "css selector", "selector": selector}
            info["note"] = f"clicked={clicked} via={via}"
            time.sleep(1.2)

        # رفرش کوتاه برای دیدن وضعیت نهایی
        try:
            driver.get(SERVER_URL)
            time.sleep(0.7)
        except Exception:
            pass

        info["network"].extend(_read_perf_log(driver))

    return info

def _keepalive_loop(interval=300):
    while not _stop_keepalive.is_set():
        try:
            if requests and SERVER_URL:
                ck = _load_cookies()
                jar = requests.cookies.RequestsCookieJar()
                for c in ck:
                    if c.get("name") and c.get("value"):
                        jar.set(c["name"], c["value"],
                                domain=c.get("domain") or "magmanode.com",
                                path=c.get("path") or "/")
                headers = {"User-Agent": UA}
                requests.get(SERVER_URL, headers=headers, cookies=jar, timeout=10)
        except Exception:
            pass
        _stop_keepalive.wait(interval)

def _arm_loop():
    while True:
        time.sleep(0.5)
        at = _schedule.get("at")
        action = _schedule.get("action")
        armed = _schedule.get("armed")
        if armed and at and time.time() >= at:
            # اجرا
            res = click_once(action or "start")
            _last_run["when"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
            _last_run["result"] = res
            # ریست تایمر
            _schedule["at"] = None
            _schedule["action"] = None
            _schedule["armed"] = False

# استارت حلقه‌ی آرمینگ
threading.Thread(target=_arm_loop, daemon=True).start()

# ===== Routes =====
@APP.get("/")
def home():
    return Response(
        """<html><body style="font-family: sans-serif">
<h2>Render Diagnostic</h2>
<p><b>۱) ورود دستی/کوکی:</b> <a href="/cookie">/cookie</a></p>
<p><b>۲) تست دستی:</b> <a href="/diag">/diag</a> |
 <a href="/diag?action=start">start</a> | <a href="/diag?action=stop">stop</a></p>
<p><b>۳) زمان‌بندی خودکار:</b> مثال:
 <code>/arm?action=start&after=60</code> (۶۰ ثانیه بعد کلیک)</p>
<p>وضعیت زمان‌بندی/آخرین اجرا: <a href="/watch">/watch</a></p>
<p>JSON دیباگ: <a href="/diag?format=json">/diag?format=json</a> |
 وضعیت سرویس: <a href="/api/status">/api/status</a></p>
</body></html>""",
        mimetype="text/html",
    )

@APP.get("/diag")
def diag():
    action = (request.args.get("action") or "").strip().lower()
    fmt = (request.args.get("format") or "").strip().lower()
    data = click_once(action) if action else click_once("")

    if fmt == "json":
        return jsonify(data)

    lines = [
        "Diag",
        f"ok: {data.get('ok')}",
        "",
        f"action: {data.get('action','')}",
        f"server_url: {data.get('server_url','')}",
        f"cookies_count: {data.get('cookies_count')}",
        f"has_start: {data.get('has_start')} | has_stop: {data.get('has_stop')}",
        f"selector: {json.dumps(data.get('selector')) if data.get('selector') else 'None'}",
        f"note: {data.get('note','')}",
        "network:",
    ] + data.get("network", [])
    return Response("\n".join(lines) + "\n\nJSON | /api/status", mimetype="text/plain")

@APP.get("/api/status")
def api_status():
    self_url = request.host_url.rstrip("/")
    magma_status = None
    magma_ok = False
    if requests and SERVER_URL:
        try:
            ck = _load_cookies()
            jar = requests.cookies.RequestsCookieJar()
            for c in ck:
                if c.get("name") and c.get("value"):
                    jar.set(c["name"], c["value"],
                            domain=c.get("domain") or "magmanode.com",
                            path=c.get("path") or "/")
            headers = {"User-Agent": UA}
            r = requests.get(SERVER_URL, headers=headers, cookies=jar, timeout=8)
            magma_status = r.status_code
            magma_ok = (200 <= r.status_code < 400)
        except Exception:
            magma_status = None
            magma_ok = False

    return jsonify({
        "service": "render_diag",
        "version": "v5",
        "ok": True,
        "pages": {
            "self": {"ok": True, "status": 200, "url": self_url},
            "magma": {"ok": magma_ok, "status": magma_status, "url": SERVER_URL},
        }
    })

@APP.route("/cookie", methods=["GET", "POST"])
def cookie():
    if request.method == "POST":
        raw = (request.form.get("cookies") or "").strip()
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                data = [data]
        except Exception:
            return Response("❌ JSON نامعتبر است.", mimetype="text/plain", status=400)
        try:
            with open(COOKIE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            return redirect("/cookie?saved=1")
        except Exception as e:
            return Response(f"❌ ذخیره نشد: {e}", mimetype="text/plain", status=500)

    existing = ""
    if os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                existing = f.read()
        except Exception:
            existing = ""
    elif ENV_COOKIES:
        existing = ENV_COOKIES

    html = f"""<html><body style="font-family:sans-serif;max-width:820px;margin:24px auto">
<h2>تنظیم کوکی (لاگینِ دستی)</h2>
<ol>
  <li>در مرورگر خودت به <b>magmanode.com</b> لاگین کن.</li>
  <li>از DevTools مقدار <code>PHPSESSID</code> را بردار.</li>
  <li>اینجا این JSON را بچسبان و ذخیره کن:</li>
</ol>
<pre>[{{"domain":"magmanode.com","name":"PHPSESSID","value":"<i>VALUE</i>","path":"/","secure":true,"httpOnly":true}}]</pre>
<form method="post">
  <textarea name="cookies" style="width:100%;height:220px">{existing}</textarea><br/>
  <button type="submit">ذخیره</button>
</form>
<p><a href="/diag">/diag</a> | <a href="/arm?action=start&after=60">arm start in 60s</a></p>
</body></html>"""
    return Response(html, mimetype="text/html")

@APP.get("/arm")
def arm():
    action = (request.args.get("action") or "start").strip().lower()
    after = int(request.args.get("after") or "60")
    _schedule["at"] = time.time() + max(1, after)
    _schedule["action"] = action if action in ("start", "stop") else "start"
    _schedule["armed"] = True
    return jsonify({"ok": True, "armed": True, "action": _schedule["action"], "after_s": after})

@APP.get("/watch")
def watch():
    left = None
    if _schedule["armed"] and _schedule["at"]:
        left = max(0, int(_schedule["at"] - time.time()))
    return jsonify({
        "armed": _schedule["armed"],
        "action": _schedule["action"],
        "seconds_left": left,
        "last_run": _last_run
    })

@APP.get("/keepalive/start")
def keepalive_start():
    if hasattr(keepalive_start, "_thr") and keepalive_start._thr.is_alive():
        return Response("Already running.", mimetype="text/plain")
    _stop_keepalive.clear()
    keepalive_start._thr = threading.Thread(target=_keepalive_loop, kwargs={"interval": 300}, daemon=True)
    keepalive_start._thr.start()
    return Response("KeepAlive started (every 5m).", mimetype="text/plain")

@APP.get("/keepalive/stop")
def keepalive_stop():
    _stop_keepalive.set()
    return Response("KeepAlive stopped.", mimetype="text/plain")

@APP.get("/whoami")
def whoami():
    return jsonify({
        "ua": request.headers.get("User-Agent", ""),
        "env_ok": True,
        "cookies_count": len(_load_cookies())
    })

if __name__ == "__main__":
    APP.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
