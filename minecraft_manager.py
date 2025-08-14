import os
import time
import random
import json
import threading
from datetime import datetime, timedelta
import logging

from flask import Flask, render_template, jsonify, request

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

# ===== تنظیمات عمومی =====
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("minecraft_manager")

STATUS_FILE = 'server_status.json'

# تنظیمات از محیط Render
MAGMA_SERVER_URL = os.environ.get("MAGMANODE_SERVER_URL", "https://magmanode.com/server?id=770999")
COOKIES_JSON = os.environ.get("MAGMANODE_COOKIES_JSON", "")

CHECK_MIN_MINUTES = float(os.environ.get("CHECK_MIN_MINUTES", "1"))
CHECK_MAX_MINUTES = float(os.environ.get("CHECK_MAX_MINUTES", "3"))

CHROME_BIN = os.environ.get("CHROME_BIN", "/usr/bin/chromium")
CHROMEDRIVER_PATH = os.environ.get("CHROMEDRIVER_PATH", "/usr/bin/chromedriver")

# Flask: قالب در همین مسیر (dashboard.html بدون تغییر)
app = Flask(__name__, template_folder=".")

class MinecraftServerManager:
    def __init__(self):
        self.driver = None
        self.click_count = 0
        self.failed_clicks = 0
        self.successful_clicks = 0
        self.start_time = datetime.now()
        self.last_known_status = None
        self.auto_click_active = True
        self.monitoring_active = True
        self.is_ready = False

        # وضعیت اولیه
        self.status = {
            'status': 'initializing',
            'last_check': None,
            'next_check': None,
            'last_action': None,
            'auto_check_active': True,
            'check_interval_minutes': random.uniform(CHECK_MIN_MINUTES, CHECK_MAX_MINUTES),
            'click_count': 0,
            'successful_clicks': 0,
            'failed_clicks': 0,
            'uptime': '0:00:00',
            'last_status_change': None,
            'start_button_available': False,
            'stop_button_available': False,
            'current_url': ''
        }

        self._setup_driver_headless()
        # تلاش برای تزریق کوکی‌ها (اگر وجود داشته باشد)
        if COOKIES_JSON:
            self._inject_cookies_if_any(COOKIES_JSON, MAGMA_SERVER_URL)
        else:
            logger.warning("کوکی‌های MAGMANODE_COOKIES_JSON تنظیم نشده‌اند؛ احتمال ری‌دایرکت به /login.")

    def _chrome_options(self):
        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--disable-extensions")
        opts.add_argument("--window-size=1366,768")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        if CHROME_BIN and os.path.exists(CHROME_BIN):
            opts.binary_location = CHROME_BIN
        opts.add_argument("--disable-blink-features=AutomationControlled")
        return opts

    def _setup_driver_headless(self):
        try:
            service = Service(CHROMEDRIVER_PATH)
            self.driver = webdriver.Chrome(service=service, options=self._chrome_options())
            try:
                self.driver.execute_cdp_cmd(
                    "Page.addScriptToEvaluateOnNewDocument",
                    {"source": "Object.defineProperty(navigator,'webdriver',{get:() => undefined});"},
                )
            except Exception as e:
                logger.debug(f"Stealth script error: {e}")
            logger.info("✅ Chrome headless راه‌اندازی شد.")
        except Exception as e:
            logger.error(f"❌ خطا در راه‌اندازی Chrome headless: {e}")
            raise

    def _domain_root(self, url: str) -> str:
        from urllib.parse import urlparse
        p = urlparse(url)
        return f"{p.scheme}://{p.hostname}"

    def _inject_cookies_if_any(self, cookies_json: str, base_url: str):
        try:
            cookies = json.loads(cookies_json)
            if not isinstance(cookies, list):
                raise ValueError("cookies json must be a list")
        except Exception as e:
            logger.error(f"فرمت کوکی‌ها نامعتبر است: {e}")
            return

        root = self._domain_root(base_url)
        self.driver.get(root)
        time.sleep(1)

        added = 0
        for c in cookies:
            try:
                name = c.get("name")
                value = c.get("value")
                if not name or value is None:
                    continue
                cookie_dict = {
                    "name": name,
                    "value": value,
                    "path": c.get("path", "/"),
                    "domain": c.get("domain", None),
                    "secure": c.get("secure", True),
                    "httpOnly": c.get("httpOnly", False),
                }
                if "expires" in c or "expiry" in c:
                    cookie_dict["expiry"] = int(c.get("expires") or c.get("expiry"))
                # اگر domain نداشت، اتوماتیک روی دامنه فعلی ست می‌شود
                self.driver.add_cookie(cookie_dict)
                added += 1
            except Exception as e:
                logger.debug(f"خطا در افزودن کوکی: {e}")

        logger.info(f"✅ {added} کوکی تزریق شد.")

    def _save_status_to_file(self):
        self.status['last_check'] = datetime.now().isoformat()
        self.status['click_count'] = self.click_count
        self.status['successful_clicks'] = self.successful_clicks
        self.status['failed_clicks'] = self.failed_clicks
        uptime_delta = datetime.now() - self.start_time
        hours = int(uptime_delta.total_seconds() // 3600)
        minutes = int((uptime_delta.total_seconds() % 3600) // 60)
        seconds = int(uptime_delta.total_seconds() % 60)
        self.status['uptime'] = f"{hours}:{minutes:02d}:{seconds:02d}"
        try:
            with open(STATUS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.status, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"❌ خطا در ذخیره وضعیت: {e}")

    def _update_next_check_time(self):
        if self.status['auto_check_active']:
            interval_minutes = self.status['check_interval_minutes']
            next_check_time = datetime.now() + timedelta(minutes=interval_minutes)
            self.status['next_check'] = next_check_time.isoformat()

    def _check_button_exists(self, button_type: str):
        try:
            if button_type == 'start':
                selectors = [
                    'button[data-action="start"]',
                    'button.bg-green-600',
                    'button[class*="bg-green-600"]'
                ]
            else:
                selectors = [
                    'button[data-action="stop"]',
                    'button.bg-red-600',
                    'button[class*="bg-red-600"]'
                ]
            for s in selectors:
                try:
                    el = self.driver.find_element(By.CSS_SELECTOR, s)
                    if el.is_displayed() and el.is_enabled():
                        return True
                except Exception:
                    continue
            return False
        except Exception:
            return False

    def _get_server_status(self) -> str:
        """کوشش برای تشخیص وضعیت سرور با متن یا دکمه‌ها"""
        try:
            # اگر هنوز صفحهٔ سرور لود نشده، برو
            if "magmanode.com" not in (self.driver.current_url or ""):
                self.driver.get(MAGMA_SERVER_URL)
                time.sleep(3)

            self.status['current_url'] = self.driver.current_url

            url_l = (self.driver.current_url or "").lower()
            if "/login" in url_l:
                detected = 'offline'  # از منظر «ما خارجیم»؛ اما بهتر اینکه unknown بگیریم
                logger.warning("به صفحهٔ login ری‌دایرکت شدیم؛ احتمالاً کوکی‌ها نامعتبرند.")
                return 'unknown'

            # خواندن متن وضعیت
            status_selectors = [
                'span[data-server-status]',
                'span.font-medium[data-server-status]',
                '.server-status',
                '.status-indicator',
                'span.font-medium'
            ]
            status_text = ""
            for s in status_selectors:
                try:
                    el = self.driver.find_element(By.CSS_SELECTOR, s)
                    if el and el.text.strip():
                        status_text = el.text.strip().lower()
                        break
                except Exception:
                    continue

            start_exists = self._check_button_exists('start')
            stop_exists = self._check_button_exists('stop')

            self.status['start_button_available'] = start_exists
            self.status['stop_button_available'] = stop_exists

            detected_status = 'unknown'
            if 'running' in status_text:
                detected_status = 'running'
            elif 'offline' in status_text:
                detected_status = 'offline'
            elif 'starting' in status_text:
                detected_status = 'starting'
            else:
                # استنتاج از روی دکمه‌ها
                if start_exists and not stop_exists:
                    detected_status = 'offline'
                elif stop_exists and not start_exists:
                    detected_status = 'running'

            if self.last_known_status != detected_status:
                logger.info(f"🔄 تغییر وضعیت: {self.last_known_status} → {detected_status}")
                self.last_known_status = detected_status
                self.status['last_status_change'] = datetime.now().isoformat()

            return detected_status
        except Exception as e:
            logger.error(f"❌ خطا در تشخیص وضعیت: {e}")
            return 'unknown'

    def _find_start_button(self):
        selectors = [
            (By.CSS_SELECTOR, 'button[data-action="start"]'),
            (By.CSS_SELECTOR, 'button.bg-green-600'),
            (By.XPATH, '//button[contains(text(),"START")]'),
            (By.XPATH, '//button[text()="START"]'),
            (By.CSS_SELECTOR, 'button.bg-green-600.text-white'),
            (By.CSS_SELECTOR, 'button[type="submit"].bg-green-600'),
            (By.CSS_SELECTOR, 'button[class*="bg-green-600"]'),
            (By.CSS_SELECTOR, 'button[class*="bg-green"][class*="text-white"]'),
            (By.XPATH, '//button[contains(@class, "bg-green")]'),
            (By.XPATH, '//button[contains(text(), "Start")]'),
            (By.XPATH, '//button[contains(text(), "شروع")]'),
        ]
        for by, sel in selectors:
            try:
                el = WebDriverWait(self.driver, 2).until(EC.element_to_be_clickable((by, sel)))
                text = (el.text or "").strip().upper()
                if any(k in text for k in ['START', 'شروع']):
                    return el
            except Exception:
                continue
        raise Exception("دکمه START پیدا نشد.")

    def _perform_click(self, button):
        try:
            self.driver.execute_script("arguments[0].scrollIntoView({behavior:'smooth',block:'center'});", button)
            time.sleep(random.uniform(0.5, 1.5))
            methods = [
                lambda: button.click(),
                lambda: self.driver.execute_script("arguments[0].click();", button),
                lambda: self.driver.execute_script(
                    "arguments[0].dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}));", button
                ),
            ]
            for i, m in enumerate(methods, start=1):
                try:
                    m()
                    self.successful_clicks += 1
                    logger.info(f"✅ کلیک موفق با روش {i}")
                    return True
                except Exception:
                    continue
            self.failed_clicks += 1
            logger.error("❌ هیچ روش کلیک کار نکرد.")
            return False
        except Exception as e:
            self.failed_clicks += 1
            logger.error(f"❌ خطا در کلیک: {e}")
            return False

    def _get_random_wait_time(self):
        minutes = random.uniform(CHECK_MIN_MINUTES, CHECK_MAX_MINUTES)
        seconds = minutes * 60
        logger.info(f"⏰ انتظار برای {minutes:.1f} دقیقه ({int(seconds)} ثانیه)")
        return seconds

    def continuous_monitoring(self):
        logger.info("👁️ شروع مانیتورینگ مداوم...")
        while self.monitoring_active:
            try:
                current_status = self._get_server_status()
                self.status['status'] = current_status
                self._update_next_check_time()
                self._save_status_to_file()
                time.sleep(10)
            except Exception as e:
                logger.error(f"❌ خطا در مانیتورینگ: {e}")
                time.sleep(30)

    def run_auto_clicker(self, url=None, max_clicks=None):
        try:
            logger.info("🚀 شروع Auto Clicker...")
            if url:
                self.driver.get(url)
                time.sleep(5)

            # وضعیت اولیه
            initial_status = self._get_server_status()
            self.status['status'] = initial_status
            self._update_next_check_time()
            self._save_status_to_file()

            # ترد مانیتورینگ
            mt = threading.Thread(target=self.continuous_monitoring, daemon=True)
            mt.start()

            self.is_ready = True
            logger.info("✅ سیستم آماده شد. حلقهٔ کلیکر شروع شد.")

            while self.auto_click_active:
                try:
                    curr = self._get_server_status()
                    # اگر سرور روشن است، صرفاً صبر کن
                    if curr == 'running':
                        time.sleep(self._get_random_wait_time())
                        continue

                    # اگر آف‌لاین است، تلاش برای START
                    if curr in ('offline', 'unknown', 'starting'):
                        try:
                            btn = self._find_start_button()
                            if self._perform_click(btn):
                                self.click_count += 1
                                self.status['last_action'] = f"START @ {datetime.now().strftime('%H:%M:%S')}"
                                self._save_status_to_file()
                                # کمی صبر کن تا وضعیت تغییر کند
                                time.sleep(15)
                        except Exception as e:
                            self.failed_clicks += 1
                            logger.error(f"❌ پیدا/کلیک دکمه START: {e}")

                    if max_clicks and self.successful_clicks >= max_clicks:
                        logger.info("✅ حد اکثر کلیک انجام شد.")
                        break

                    time.sleep(self._get_random_wait_time())
                except Exception as e:
                    logger.error(f"❌ خطا در حلقهٔ اصلی: {e}")
                    time.sleep(30)
        finally:
            logger.info("Auto clicker پایان یافت.")

    def start_server_manual(self):
        try:
            curr = self._get_server_status()
            if curr == 'running':
                return False, "سرور همین الان روشن است."
            btn = self._find_start_button()
            ok = self._perform_click(btn)
            if ok:
                self.status['last_action'] = f"START manual @ {datetime.now().strftime('%H:%M:%S')}"
                self._save_status_to_file()
                time.sleep(10)
                return True, "درخواست روشن شدن ارسال شد."
            return False, "کلیک روی START ناموفق بود."
        except Exception as e:
            return False, f"خطا: {e}"

    def stop_server_manual(self):
        try:
            stop_selectors = [
                'button[data-action="stop"]',
                'button.bg-red-600',
                'button[class*="bg-red-600"]'
            ]
            stop_btn = None
            for s in stop_selectors:
                try:
                    b = self.driver.find_element(By.CSS_SELECTOR, s)
                    if b.is_displayed() and b.is_enabled():
                        stop_btn = b
                        break
                except Exception:
                    continue
            if not stop_btn:
                return False, "دکمه STOP پیدا نشد."
            stop_btn.click()
            self.status['last_action'] = f"STOP manual @ {datetime.now().strftime('%H:%M:%S')}"
            self._save_status_to_file()
            return True, "درخواست خاموشی ارسال شد."
        except Exception as e:
            return False, f"خطا: {e}"

    def toggle_auto_check(self, active: bool):
        self.status['auto_check_active'] = active
        if active:
            self._update_next_check_time()
        else:
            self.status['next_check'] = None
        self._save_status_to_file()
        return True, f"بررسی خودکار {'فعال' if active else 'غیرفعال'} شد"

    def set_check_interval(self, min_minutes, max_minutes):
        self.status['check_interval_minutes'] = random.uniform(min_minutes, max_minutes)
        if self.status['auto_check_active']:
            self._update_next_check_time()
        self._save_status_to_file()
        return True, f"فاصلهٔ بررسی: {min_minutes}-{max_minutes} دقیقه"

    def get_detailed_status(self):
        curr = self._get_server_status()
        self.status['status'] = curr
        self.status['current_url'] = self.driver.current_url if self.driver else ''
        self.status['start_button_available'] = self._check_button_exists('start')
        self.status['stop_button_available'] = self._check_button_exists('stop')
        return self.status

    def close(self):
        self.auto_click_active = False
        self.monitoring_active = False
        try:
            if self.driver:
                self.driver.quit()
        except Exception:
            pass


server_manager = None


def load_status_from_file():
    try:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {
        'status': 'initializing',
        'last_check': None,
        'next_check': None,
        'last_action': None,
        'auto_check_active': True,
        'check_interval_minutes': 2,
        'click_count': 0,
        'successful_clicks': 0,
        'failed_clicks': 0,
        'uptime': '0:00:00',
        'last_status_change': None,
        'start_button_available': False,
        'stop_button_available': False,
        'current_url': ''
    }


@app.route("/")
def dashboard():
    status = load_status_from_file()
    return render_template("dashboard.html", status=status)


@app.route("/api/status")
def api_status():
    if server_manager and server_manager.is_ready:
        status = server_manager.get_detailed_status()
        server_manager._save_status_to_file()
    else:
        status = load_status_from_file()
    return jsonify(status)


@app.route("/api/start", methods=["POST"])
def api_start():
    if not server_manager or not server_manager.is_ready:
        return jsonify({'success': False, 'message': 'سیستم هنوز آماده نشده است'})
    ok, msg = server_manager.start_server_manual()
    return jsonify({'success': ok, 'message': msg})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    if not server_manager or not server_manager.is_ready:
        return jsonify({'success': False, 'message': 'سیستم هنوز آماده نشده است'})
    ok, msg = server_manager.stop_server_manual()
    return jsonify({'success': ok, 'message': msg})


@app.route("/api/toggle_auto_check", methods=["POST"])
def api_toggle():
    if not server_manager or not server_manager.is_ready:
        return jsonify({'success': False, 'message': 'سیستم هنوز آماده نشده است'})
    data = request.json or {}
    active = bool(data.get("active", True))
    ok, msg = server_manager.toggle_auto_check(active)
    return jsonify({'success': ok, 'message': msg})


@app.route("/api/set_check_interval", methods=["POST"])
def api_set_interval():
    if not server_manager or not server_manager.is_ready:
        return jsonify({'success': False, 'message': 'سیستم هنوز آماده نشده است'})
    data = request.json or {}
    min_m = int(data.get("min", CHECK_MIN_MINUTES))
    max_m = int(data.get("max", CHECK_MAX_MINUTES))
    ok, msg = server_manager.set_check_interval(min_m, max_m)
    return jsonify({'success': ok, 'message': msg})


@app.route("/api/force_check", methods=["POST"])
def api_force():
    if not server_manager or not server_manager.is_ready:
        return jsonify({'success': False, 'message': 'سیستم هنوز آماده نشده است'})
    try:
        status = server_manager.get_detailed_status()
        server_manager._save_status_to_file()
        return jsonify({'success': True, 'status': status, 'message': 'بررسی انجام شد'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'خطا: {e}'}), 500


def run_server_manager():
    global server_manager
    try:
        server_manager = MinecraftServerManager()
        server_manager.run_auto_clicker(url=MAGMA_SERVER_URL, max_clicks=None)
    except Exception as e:
        logger.error(f"❌ خطا در اجرای مدیر: {e}")


def main():
    logger.info("🚀 راه‌اندازی سیستم مدیریت سرور (Render)")
    # ترد بک‌گراند برای کلیکر
    t = threading.Thread(target=run_server_manager, daemon=True)
    t.start()
    # وب‌سرور
    port = int(os.environ.get("PORT", "5000"))
    app.run(debug=False, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
