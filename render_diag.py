import os, json, time, traceback, logging
from flask import Flask, request, jsonify, Response

# === Selenium ===
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

app = Flask(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("render_diag")

def env_get(name, *alts, default=None):
    for k in (name,) + alts:
        v = os.getenv(k)
        if v:
            return v
    return default

def parse_cookies(raw):
    if not raw:
        return [], "NO_COOKIES_SET"
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        out = []
        allow = {"name","value","domain","path","expiry","secure","httpOnly"}
        for c in data:
            # فقط کلیدهای مجاز Selenium
            out.append({k: c[k] for k in allow if k in c})
        return out, None
    except Exception as e:
        return [], f"COOKIES_JSON_INVALID: {e}"

def build_driver():
    chrome_bin_candidates = ["/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]
    binary = next((p for p in chrome_bin_candidates if os.path.exists(p)), None)

    opts = Options()
    if binary:
        opts.binary_location = binary
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1280,1000")

    service_path = "/usr/bin/chromedriver"  # روی Render نصب می‌شود
    service = Service(executable_path=service_path)
    driver = webdriver.Chrome(service=service, options=opts)
    return driver, {"binary": binary or "auto", "driver": service_path}

def click_btn(driver, action):
    selector = f'button[data-action="{action}"]'
    btn = WebDriverWait(driver, 12).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
    )
    btn.click()
    return {"clicked": True, "selector": selector}

def run_diag(action):
    server_url = env_get("MAGMANODE_SERVER_URL", "PANEL_URL", "PANEL_URL1", "PANEL_URL2")
    cookies_raw = env_get("MAGMANODE_COOKIES_JSON", "COOKIES_JSON")
    cookies, cookies_err = parse_cookies(cookies_raw)

    diag = {
        "server_url": server_url,
        "env_ok": bool(server_url and cookies_raw),
        "cookies_count": len(cookies),
        "cookies_error": cookies_err,
        "action": action or "",
        "steps": []
    }

    if not server_url:
        diag["error"] = "NO_SERVER_URL_SET"
        return diag
    if cookies_err:
        diag["error"] = cookies_err
        return diag

    driver, meta = build_driver()
    diag["chrome_meta"] = meta
    try:
        driver.get(server_url)

        # تزریق کوکی‌ها
        for c in cookies:
            try:
                if "path" not in c:
                    c["path"] = "/"
                driver.add_cookie(c)
            except Exception as e:
                diag["steps"].append({"add_cookie_failed": str(e), "cookie": c.get("name")})

        driver.refresh()
        time.sleep(1.2)

        if action in ("start", "stop"):
            step = click_btn(driver, action)
            diag["steps"].append(step)
            diag["ok"] = True
        else:
            diag["note"] = "no action, opened and injected cookies"
            diag["ok"] = True
    finally:
        driver.quit()

    return diag

@app.route("/")
def home():
    return Response("""
<h3>Render Diagnostic</h3>
<ul>
  <li><a href="/diag">/diag</a></li>
  <li><a href="/diag?format=json">/diag?format=json</a></li>
  <li>کلیک: <a href="/diag?action=start">START</a> | <a href="/diag?action=stop">STOP</a></li>
</ul>
""", mimetype="text/html")

@app.route("/api/status")
def api_status():
    return jsonify({"ok": True, "service": "render_diag", "ts": int(time.time())})

@app.route("/diag")
def diag():
    action = request.args.get("action", "")
    as_json = request.args.get("format") == "json"
    try:
        result = run_diag(action)
        if as_json:
            return jsonify(result)
        return Response("<pre>" + json.dumps(result, ensure_ascii=False, indent=2) + "</pre>",
                        mimetype="text/html")
    except Exception as e:
        tb = traceback.format_exc()
        log.exception("diag failed")
        payload = {"ok": False, "error": str(e), "trace": tb}
        if as_json or os.getenv("DEBUG") == "1":
            return jsonify(payload), 500
        return Response("<h4>Internal Error</h4><pre>"+tb+"</pre>", status=500, mimetype="text/html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
