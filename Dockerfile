# Dockerfile برای استقرار در Render با Chrome هدلس + ChromeDriver
FROM python:3.11-slim

# جلوگیری از نوشتن فایل‌های bytecode و برای عملکرد بهتر در محیط‌های هدلس
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# نصب Chromium و ChromeDriver و کتابخانه‌های لازم
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

# تنظیم دایرکتوری کاری در کانتینر
WORKDIR /app

# نصب وابستگی‌ها از requirements.txt
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# کد پروژه را به کانتینر کپی می‌کنیم
COPY . /app

# متغیرهای محیطی برای مسیر باینری‌های کروم
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

# پورت پیش‌فرض برای Render (10000)
ENV PORT=10000

EXPOSE 10000

# اجرای auth_checker برای بررسی کوکی‌ها و سپس راه‌اندازی وب‌سرویس minecraft_manager
CMD bash -lc "python auth_checker.py || true; python minecraft_manager.py"
