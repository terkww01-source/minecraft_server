import os
import json
import time
import logging
from urllib.parse import urlparse

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("auth_checker")

MAGMA_SERVER_URL = os.environ.get("MAGMANODE_SERVER_URL", "https://magmanode.com/server?id=770999")
COOKIES_JSON = os.environ.get("MAGMANODE_COOKIES_JSON", "")

CHROME_BIN = os.environ.get("CHROME_BIN", "/usr/bin/chromium")
CHROMEDRIVER_PATH = os.environ.get("CHROMEDRIVER_PATH", "/usr/bin/chromedriver")


def _chrome_options() -> Options:
    opts = Options()
    # هدلس برای Render
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--window-size=1366,768")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    # اگر مسیر باینری مشخص باشد
    if CHROME_BIN and os.path.exists(CHROME_BIN):
        opts.binary_location = CHROME_BIN
    # اندکی استلث
    opts.add_argument("--disable-blink-features=AutomationControlled")
    return opts


def _start_driver() -> webdriver.Chrome:
    service = Service(CHROMEDRIVER_PATH)
    driver = webdriver.Chrome(service=service, options=_chrome_options())
    # پنهان کردن navigator.webdriver
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator,'webdriver',{get:() => undefined});"},
        )
    except Exception as e:
        logger.debug(f"Stealth script error: {e}")
    return driver


def _domain_root(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.hostname}"


def _inject_cookies_if_any(driver: webdriver.Chrome, cookies_json: str, base_url: str):
    if not cookies_json:
        logger.warning("هیچ کوکی‌ای در متغیر محیطی MAGMANODE_COOKIES_JSON تنظیم نشده است.")
        return

    try:
        cookies = json.loads(cookies_json)
        if not isinstance(cookies, list):
            raise ValueError("cookies json must be a list of cookie dicts")
    except Exception as e:
        logger.error(f"فرمت کوکی‌ها نامعتبر است: {e}")
        return

    # باید یک بار وارد دامنه شویم تا add_cookie مجاز باشد
    root = _domain_root(base_url)
    driver.get(root)
    time.sleep(1)

    added = 0
    for c in cookies:
        try:
            # حداقل‌ها: name و value
            name = c.get("name")
            value = c.get("value")
            if not name or value is None:
                continue

            cookie_dict = {
                "name": name,
                "value": value,
                "path": c.get("path", "/"),
                "domain": c.get("domain", urlparse(root).hostname),
                "secure": c.get("secure", True),
                "httpOnly": c.get("httpOnly", False),
            }
            if "expires" in c:
                # برخی exporter ها از expires یا expiry استفاده می‌کنند
                cookie_dict["expiry"] = int(c.get("expires") or c.get("expiry"))

            driver.add_cookie(cookie_dict)
            added += 1
        except Exception as e:
            logger.debug(f"خطا در افزودن یک کوکی: {e}")

    logger.info(f"✅ {added} کوکی برای دامنه تزریق شد.")


def is_logged_in(driver: webdriver.Chrome, server_url: str) -> bool:
    driver.get(server_url)
    time.sleep(3)

    current = driver.current_url.lower()
    if "/login" in current:
        return False

    # یک چک ساده: اگر دکمه START یا متن وضعیت را ببینیم یعنی واردیم
    try:
        # هرکدام موجود بود، احتمالاً لاگین برقرار است
        has_start = len(driver.find_elements(By.CSS_SELECTOR, 'button[data-action="start"], button.bg-green-600')) > 0
        has_status = len(driver.find_elements(By.CSS_SELECTOR, 'span[data-server-status], .server-status, .status-indicator')) > 0
        if has_start or has_status:
            return True
    except Exception:
        pass
    # اگر ری‌دایرکت به login نبود، باز هم احتمالاً لاگین برقرار است
    return "/login" not in driver.current_url.lower()


def main():
    logger.info("🔐 شروع بررسی احراز هویت (Render/Headless)")
    driver = None
    try:
        driver = _start_driver()
        if COOKIES_JSON:
            _inject_cookies_if_any(driver, COOKIES_JSON, MAGMA_SERVER_URL)
        else:
            logger.warning("کوکی‌ها تنظیم نشده؛ احتمالاً به صفحهٔ لاگین ری‌دایرکت می‌شویم.")

        ok = is_logged_in(driver, MAGMA_SERVER_URL)
        if ok:
            logger.info("✅ ورود معتبر است؛ به صفحهٔ سرور دسترسی داریم.")
            print("YES")
            exit(0)
        else:
            logger.error("❌ هنوز وارد حساب نیستیم (redirect به login یا نبود عناصر کلیدی).")
            print("NO")
            exit(1)
    except Exception as e:
        logger.error(f"خطا در auth_checker: {e}")
        print("NO")
        exit(2)
    finally:
        try:
            if driver:
                driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
