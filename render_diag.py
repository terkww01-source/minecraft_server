import os, json, time, re
from contextlib import contextmanager
from flask import Flask, request, jsonify, Response
# اگر requests نصب نیست، بدونش هم کار می‌کنیم
try:
    import requests  # فقط برای /api/status (اختیاری)
except Exception:  # pragma: no cover
    requests = None

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

APP = Flask("render_diag")

# ======== تنظیمات عمومی ========
SERVER_URL = os.getenv("MAGMANODE_SERVER_URL", "").strip()
COOKIES_RAW = os.getenv("MAGMANODE_COOKIES_JSON", "").strip()
PROXY_URL = os.getenv("PROXY_URL", "").strip()
UA = os.getenv(
    "MAGMANODE_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
).strip()

CHROME_BIN = "/usr/bin/chromium"
CHROME_DRV = "/usr/bin/chromedriver"


def _safe_json(val, default):
    try:
        return json.loads(val) if val else default
    except Exception:
        return default


def _filter_cookie(c):
    """
    تبدیل ورودی به فرمت قابل قبول برای driver.add_cookie
    """
    out = {
        "name": c.get("name"),
        "value": c.get("value"),
        "domain": c.get("domain") or "magmanode.com",
        "path": c.get("path") or "/",
    }
    if "secure" in c:
        out["secure"] = bool(c["secure"])
    if "httpOnly" in c:
        out["httpOnly"] = bool(c["httpOnly"])
    # sameSite اختیاری
    if c.get("sameSite") in ("Lax", "None", "Strict"):
        out["sameSite"] = c["sameSite"]
    return out


def _read_perf_log(driver):
    """
    لاگ شبکه (Performance) را به صورت خوانا برمی‌گرداند.
    """
    lines = []
    try:
        for entry in driver.get_log("performance"):
            msg = json.loads(entry["message"])["message"]
            method = msg.get("method", "")
            params = msg.get("params", {})
            if method == "Network.requestWillBeSent":
                url = params.get("request", {}).get("url", "")
                lines.append(f"- REQ {params.get('request', {}).get('method','GET')} {url}")
            elif method == "Network.responseReceived":
                url = params.get("response", {}).get("url", "")
                status = params.get("response", {}).get("status", -1)
                lines.append(f"- RES {status} {url}")
    except Exception:
        pass
    return lines


def _status_from_perf(perf_lines, url_contains):
    """
    آخرین status مربوط به URL مورد نظر را از لاگ شبکه پیدا می‌کند.
    """
    status = -1
    for ln in perf_lines:
        if ln.startswith("- RES "):
            try:
                parts = ln.split(" ")
                st = int(parts[2])
                url = " ".join(parts[3:])
                if url_contains in url:
                    status = st
            except Exception:
                continue
    return status


@contextmanager
def make_driver():
    opts = Options()
    opts.binary_location = CHROME_BIN
    # هدلس و پایدارتر در کلود
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1366,768")
    opts.add_argument(f"--user-agent={UA}")
    # کاهش تشخیص اتوماسیون
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    # لاگ شبکه
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    if PROXY_URL:
        opts.add_argument(f"--proxy-server={PROXY_URL}")

    service = Service(CHROME_DRV)
    driver = webdriver.Chrome(service=service, options=opts)

    # پنهان کردن navigator.webdriver
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": 'Object.defineProperty(navigator, "webdriver", {get: () => undefined});'},
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


def inject_cookies(driver):
    cookies = _safe_json(COOKIES_RAW, [])
    if not cookies:
        return 0, None

    # باید ابتدا روی همان دامنه باشیم
    driver.get("https://magmanode.com/")
    time.sleep(0.5)
    added = 0
    err = None
    for c in cookies:
        try:
            driver.add_cookie(_filter_cookie(c))
            added += 1
        except Exception as e:
            err = str(e)
    return added, err


def ensure_consent(driver):
    """
    تلاش ساده برای بستن/اوکی‌کردن پیام‌های Consent (در حد امکان).
    نادیده بگیر اگر نبود.
    """
    try:
        time.sleep(0.5)
        # پنجره‌های داخل iframe مربوط به fundingchoices را اگر هست، دکمه‌ها را کلیک می‌کنیم
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for f in iframes:
            try:
                src = f.get_attribute("src") or ""
                if "fundingchoicesmessages.google.com" in src or "consent" in src:
                    driver.switch_to.frame(f)
                    btns = driver.find_elements(By.XPATH, "//button|//div[@role='button']")
                    for b in btns:
                        txt = (b.text or "").strip().lower()
                        if any(k in txt for k in ["accept", "agree", "continue", "allow", "ok", "i agree"]):
                            try:
                                b.click()
                                time.sleep(0.2)
                            except Exception:
                                pass
                    driver.switch_to.default_content()
            except Exception:
                driver.switch_to.default_content()
                continue
    except Exception:
        pass


def click_action(driver, action):
    """
    روی start یا stop کلیک می‌کند.
    """
    selector = f'button[data-action="{action}"]'
    clicked = False
    via = "native"
    try:
        btn = WebDriverWait(driver, 12).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
        )
        try:
            btn.click()
            clicked = True
        except Exception:
            # fallback به جاوااسکریپت
            driver.execute_script("arguments[0].click();", btn)
            clicked = True
            via = "js"
    except Exception:
        pass
    return clicked, selector, via


def run_diag(action=""):
    info = {
        "ok": True,
        "action": action,
        "server_url": SERVER_URL,
        "cookies_count": 0,
        "cookies_error": None,
        "has_start": False,
        "has_stop": False,
        "status_before": -1,
        "status_after": -1,
        "selector": None,
        "note": "",
        "network": [],
    }

    if not SERVER_URL:
        info["ok"] = True
        info["note"] = "no action, opened and injected cookies"
        return info

    with make_driver() as driver:
        # مرحله 1: کوکی
        cnt, err = inject_cookies(driver)
        info["cookies_count"] = cnt
        info["cookies_error"] = err

        # مرحله 2: رفتن به صفحه سرور
        driver.get(SERVER_URL)
        time.sleep(0.8)
        ensure_consent(driver)

        # کشف دکمه‌ها
        try:
            info["has_start"] = len(driver.find_elements(By.CSS_SELECTOR, 'button[data-action="start"]')) > 0
            info["has_stop"] = len(driver.find_elements(By.CSS_SELECTOR, 'button[data-action="stop"]')) > 0
        except Exception:
            pass

        # وضعیت قبل (از لاگ شبکه)
        perf1 = _read_perf_log(driver)
        info["network"].extend(perf1)
        info["status_before"] = _status_from_perf(perf1, "/server?id=")

        # اگر اکشن خواسته شده
        if action in ("start", "stop"):
            clicked, selector, via = click_action(driver, action)
            info["selector"] = {"by": "css selector", "selector": selector}
            info["note"] = f"clicked={clicked} via={via}"

            # کمی صبر و اسکرول برای تحریک رندر
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            except Exception:
                pass
            time.sleep(1.5)

        # دوباره صفحه را رفرش کوتاه برای دیدن درخواست‌های /power
        try:
            driver.get(SERVER_URL)
            time.sleep(1.0)
        except Exception:
            pass

        perf2 = _read_perf_log(driver)
        info["network"].extend(perf2)
        info["status_after"] = _status_from_perf(perf2, "/server?id=")

    return info


# =============== HTTP Routes ===============
@APP.get("/")
def home():
    return Response(
        """<html><body style="font-family: sans-serif">
<h2>Render Diagnostic</h2>
<p>رفتن به گزارش: <a href="/diag">/diag</a></p>
<p>JSON: <a href="/diag?format=json">/diag?format=json</a></p>
<p>کلیک و گزارش همزمان:
  <a href="/diag?action=start">start</a> |
  <a href="/diag?action=stop">stop</a>
</p>
</body></html>""",
        mimetype="text/html",
    )


@APP.get("/diag")
def diag():
    action = (request.args.get("action") or "").strip().lower()
    fmt = (request.args.get("format") or "").strip().lower()

    data = run_diag(action)

    if fmt == "json":
        return jsonify(data)

    # خروجی خوانا
    lines = [
        "Diag",
        f"ok: {data.get('ok')}",
        "",
        f"action: {data.get('action','')}",
        f"server_url: {data.get('server_url','')}",
        f"cookies_count: {data.get('cookies_count')}",
        f"has_start: {data.get('has_start')} | has_stop: {data.get('has_stop')}",
        f"status_before: {data.get('status_before')}",
        f"status_after: {data.get('status_after')}",
        f"selector: {json.dumps(data.get('selector')) if data.get('selector') else 'None'}",
        f"note: {data.get('note','')}",
        "network:",
    ]
    lines += data.get("network", [])
    return Response("\n".join(lines) + "\n\nJSON | /api/status", mimetype="text/plain")


@APP.get("/api/status")
def api_status():
    # ping self
    try:
        self_url = request.host_url.rstrip("/")
        self_ok = True
        self_status = 200
    except Exception:
        self_ok = True
        self_status = 200
        self_url = ""

    # ping magma (اختیاری با requests)
    magma_status = None
    magma_ok = False
    magma_url = SERVER_URL or ""
    if requests and SERVER_URL:
        try:
            r = requests.get(SERVER_URL, timeout=6)
            magma_status = r.status_code
            magma_ok = (200 <= r.status_code < 400)
        except Exception:
            magma_status = None
            magma_ok = False

    return jsonify({
        "service": "render_diag",
        "version": "v3",
        "ok": True,
        "pages": {
            "self": {"ok": self_ok, "status": self_status, "url": self_url},
            "magma": {"ok": magma_ok, "status": magma_status, "url": magma_url},
        }
    })


@APP.get("/whoami")
def whoami():
    # برای بررسی UA و محیط
    return jsonify({
        "ua": request.headers.get("User-Agent", ""),
        "env_ok": True,
        "cookies_count": len(_safe_json(COOKIES_RAW, [])),
    })


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    APP.run(host="0.0.0.0", port=port)
