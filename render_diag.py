import os, json, time, pathlib, traceback
from flask import Flask, request, jsonify, Response
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
import requests

app = Flask(__name__)

# ---------- تنظیمات ----------
PORT = int(os.getenv("PORT", "10000"))
SELF_HINT = os.getenv("SELF_URL", "").strip()  # اختیاری
MAGMA_URL = os.getenv("MAGMA_URL", "https://magmanode.com/server?id=770999").strip()

# کوکی‌ها را یا از env بخوان، یا از فایل cookies.json (اختیاری)
def load_cookies_for_domain():
    """
    پشتیبانی از سه حالت:
    1) env: MAGMA_COOKIES = '[{"name":"...", "value":"...", "domain":"magmanode.com"}]'
    2) env: MAGMA_COOKIE_NAME, MAGMA_COOKIE_VALUE, MAGMA_COOKIE_DOMAIN
    3) فایل ./cookies.json حاوی آرایه‌ی کوکی‌ها
    """
    env_json = os.getenv("MAGMA_COOKIES", "").strip()
    if env_json:
        try: return json.loads(env_json)
        except: pass

    name = os.getenv("MAGMA_COOKIE_NAME", "").strip()
    value = os.getenv("MAGMA_COOKIE_VALUE", "").strip()
    domain = os.getenv("MAGMA_COOKIE_DOMAIN", "magmanode.com").strip()
    if name and value:
        return [{"name": name, "value": value, "domain": domain, "path": "/"}]

    # فایل
    p = pathlib.Path("cookies.json")
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except:
            return []
    return []

def make_driver():
    chrome_bin = os.getenv("CHROME_BIN", "/usr/bin/chromium")
    chrome_drv = os.getenv("CHROME_DRIVER", "/usr/bin/chromedriver")

    opts = Options()
    # Headless پایدار
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,2000")
    opts.binary_location = chrome_bin

    driver = webdriver.Chrome(executable_path=chrome_drv, options=opts)
    driver.set_page_load_timeout(45)
    driver.implicitly_wait(0)
    return driver

def inject_cookies(driver, url, steps):
    cookies = load_cookies_for_domain()
    steps.append(f"cookies.load={len(cookies)}")
    if not cookies:
        return 0

    base = "https://magmanode.com/"
    driver.get(base)
    time.sleep(1.2)
    ok = 0
    for c in cookies:
        try:
            cc = {"name": c["name"], "value": c["value"], "path": c.get("path","/")}
            if "domain" in c: cc["domain"] = c["domain"]
            driver.add_cookie(cc)
            ok += 1
        except Exception as e:
            steps.append(f"cookies.add.error={repr(e)}")
    return ok

def multi_find(driver, action, steps):
    """چندین لوکِیتِر برای اطمینان از پیدا شدن دکمه"""
    selectors = [
        (By.CSS_SELECTOR, f'button[data-action="{action}"]'),
        (By.XPATH, f'//button[normalize-space()="{action.upper()}"]'),
        (By.XPATH, f'(//button[contains(., "{action.upper()}")])[1]'),
        (By.XPATH, f'//button[contains(@class,"bg-{"green" if action=="start" else "red"}-600")]'),
    ]
    last_err = None
    for by, sel in selectors:
        try:
            steps.append(f"wait.clickable:{by}={sel}")
            btn = WebDriverWait(driver, 12).until(EC.element_to_be_clickable((by, sel)))
            return btn, (by, sel)
        except Exception as e:
            last_err = e
            steps.append(f"miss:{by}={sel}")
    raise last_err if last_err else TimeoutException("button not found")

def click_button(driver, action, steps):
    # اسکرول آرام بالا تا پایین برای رندر
    driver.execute_script("window.scrollTo(0,0)")
    time.sleep(0.4)
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.4)")
    time.sleep(0.4)

    btn, used = multi_find(driver, action, steps)
    steps.append(f"found:{used}")

    # اول try با click معمولی
    try:
        btn.click()
        steps.append("clicked:native")
        return True
    except Exception as e:
        steps.append(f"native_click_fail:{repr(e)}")

    # بعد JS click
    try:
        driver.execute_script("arguments[0].click();", btn)
        steps.append("clicked:js")
        return True
    except Exception as e:
        steps.append(f"js_click_fail:{repr(e)}")

    # آخر: focus + enter
    try:
        driver.execute_script("arguments[0].focus();", btn)
        time.sleep(0.2)
        btn.send_keys("\n")
        steps.append("clicked:enter")
        return True
    except Exception as e:
        steps.append(f"enter_click_fail:{repr(e)}")
        return False

def snap(driver, name):
    pathlib.Path("static").mkdir(exist_ok=True)
    path = f"static/{name}"
    try:
        driver.save_screenshot(path)
        return path
    except:
        return None

def page_has_start_stop_by_html(html):
    has_start = 'data-action="start"' in html or ">START<" in html
    has_stop  = 'data-action="stop"'  in html or ">STOP<"  in html
    return {"has_start": has_start, "has_stop": has_stop}

def run_diag(action: str):
    steps = []
    info = {
        "server_url": MAGMA_URL,
        "env_ok": True,
        "cookies_count": 0,
        "cookies_error": None,
        "action": action,
        "chrome_meta": {"binary": "/usr/bin/chromium", "driver": "/usr/bin/chromedriver"},
        "ok": False,
        "steps": steps,
        "shots": {},
    }

    drv = None
    try:
        drv = make_driver()
        steps.append("driver.ok")

        # کوکی
        added = inject_cookies(drv, MAGMA_URL, steps)
        info["cookies_count"] = added

        # رفتن به صفحه
        url = MAGMA_URL.strip()
        steps.append(f"goto:{url}")
        drv.get(url)

        # منتظر وجود body
        WebDriverWait(drv, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(0.8)

        info["shots"]["before"] = snap(drv, "diag_before.png")

        # اگر اکشن داریم کلیک کن
        if action in ("start", "stop"):
            ok = click_button(drv, action, steps)
            steps.append(f"click_result={ok}")
            time.sleep(2.0)
            info["shots"]["after"] = snap(drv, "diag_after.png")
        else:
            steps.append("no_action")

        # وضعیت حضور دکمه‌ها در HTML
        html = drv.page_source
        info.update(page_has_start_stop_by_html(html))
        info["ok"] = True
        return info

    except Exception as e:
        info["ok"] = False
        info["error"] = repr(e)
        info["trace"] = traceback.format_exc()
        return info
    finally:
        try:
            if drv: drv.quit()
        except:
            pass

def check_pages():
    """دو لینک: 1) همین سرویس، 2) صفحه Magma  — فقط در حد up بودن و (در صورت دسترسی) نشانه دکمه‌ها"""
    out = {"self": {}, "magma": {}}

    # 1) وضعیت خود سرویس
    try:
        base = SELF_HINT or request.host_url
        r = requests.get(base, timeout=8)
        out["self"] = {"url": base, "status": r.status_code, "ok": r.ok}
    except Exception as e:
        out["self"] = {"url": SELF_HINT or request.host_url, "error": repr(e)}

    # 2) وضعیت صفحه Magma (اگر لاگین لازم باشد ممکن است 302/401 بیاید—همین را گزارش می‌کنیم)
    try:
        r2 = requests.get(MAGMA_URL, timeout=10, allow_redirects=True)
        html_snip = r2.text[:2000]
        signs = page_has_start_stop_by_html(html_snip)
        out["magma"] = {"url": MAGMA_URL, "status": r2.status_code, **signs}
    except Exception as e:
        out["magma"] = {"url": MAGMA_URL, "error": repr(e)}

    return out

# ---------- Routes ----------
@app.get("/")
def root():
    return Response(
        """<h2>Render Diagnostic</h2>
<ul>
<li>رفتن به گزارش: <a href="/diag">/diag</a></li>
<li>JSON: <a href="/diag?format=json">/diag?format=json</a></li>
<li>کلیک و گزارش همزمان: <a href="/diag?action=start">start</a> | <a href="/diag?action=stop">stop</a></li>
<li>وضعیت دو لینک: <a href="/api/status">/api/status</a></li>
</ul>
""",
        mimetype="text/html"
    )

@app.get("/api/status")
def api_status():
    return jsonify({
        "ok": True,
        "service": "render_diag",
        "version": "v2",
        "pages": check_pages(),
    })

@app.get("/diag")
def diag():
    action = (request.args.get("action") or "").strip().lower()
    fmt = (request.args.get("format") or "").strip().lower()

    result = run_diag(action if action in ("start","stop") else "")
    if fmt == "json":
        return jsonify(result)

    # HTML ساده با لینک‌ها و شات‌ها
    rows = "".join([f"<li>{i+1}. {step}</li>" for i, step in enumerate(result.get("steps", []))])
    shots = result.get("shots", {})
    before = shots.get("before")
    after  = shots.get("after")

    html = f"""
<h3>Diag</h3>
<p><b>ok:</b> {result.get('ok')}</p>
<p><b>action:</b> {result.get('action')}</p>
<p><b>server_url:</b> {result.get('server_url')}</p>
<p><b>cookies_count:</b> {result.get('cookies_count')}</p>
<p><b>has_start:</b> {result.get('has_start', False)} | <b>has_stop:</b> {result.get('has_stop', False)}</p>
{"<p style='color:red'><b>error:</b> "+result.get("error","")+"</p>" if not result.get("ok") else ""}
<ul>{rows}</ul>
<div style="display:flex;gap:16px">
  <div><p><b>before</b></p>{f"<img src='/{before}' style='max-width:480px'>" if before else "-"}</div>
  <div><p><b>after</b></p>{f"<img src='/{after}' style='max-width:480px'>" if after else "-"}</div>
</div>
<p><a href="/diag?format=json">JSON</a> | <a href="/api/status">/api/status</a></p>
"""
    return Response(html, mimetype="text/html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
