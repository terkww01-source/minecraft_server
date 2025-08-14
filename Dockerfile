# Dockerfile برای استقرار در Render با Chrome هدلس + ChromeDriver
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# نصب Chromium و ChromeDriver و کتابخانه‌های لازم
# تغییر به این صورت
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    fonts-liberation \
    libnss3 \
    libgdk-pixbuf-xlib-2.0-0 \  # تغییر به این
    libasound2 \
    libxss1 \
    libgtk-3-0 \
    libgbm1 \
    curl  && rm -rf /var/lib/apt/lists/*


WORKDIR /app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# کد پروژه
COPY . /app

# متغیرهای مسیر باینری‌های کروم
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

# Render پورت را از طریق PORT می‌دهد؛ پیش‌فرض 10000
ENV PORT=10000

EXPOSE 10000

# ابتدا auth_checker اجرا شود (بررسی کوکی‌ها)،
# سپس حتی اگر auth ناموفق بود، وب‌سرویس برای داشبورد بالا بیاید.
CMD bash -lc "python auth_checker.py || true; python minecraft_manager.py"
