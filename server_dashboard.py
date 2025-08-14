from flask import Flask, render_template, jsonify, request
import threading
import time
import random
from datetime import datetime, timedelta
import json
import os

app = Flask(__name__, template_folder=".")

server_status = {
    'status': 'unknown',
    'last_check': None,
    'next_check': None,
    'last_action': None,
    'auto_check_active': True,
    'check_interval_minutes': random.uniform(1, 5),
    'click_count': 0,
    'successful_clicks': 0,
    'failed_clicks': 0,
    'uptime': '0:00:00',
    'last_status_change': None,
    'start_button_available': False,
    'stop_button_available': False
}

STATUS_FILE = 'server_status.json'

def load_status():
    global server_status
    try:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, 'r', encoding='utf-8') as f:
                server_status = json.load(f)
    except Exception as e:
        print(f"خطا در بارگذاری وضعیت: {e}")

def save_status():
    try:
        with open(STATUS_FILE, 'w', encoding='utf-8') as f:
            json.dump(server_status, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"خطا در ذخیره وضعیت: {e}")

def update_server_status(new_status):
    server_status['status'] = new_status
    server_status['last_check'] = datetime.now().isoformat()
    if server_status['auto_check_active']:
        interval_minutes = server_status['check_interval_minutes']
        next_check_time = datetime.now() + timedelta(minutes=interval_minutes)
        server_status['next_check'] = next_check_time.isoformat()
    save_status()

def auto_check_thread():
    while True:
        if server_status['auto_check_active']:
            current_time = datetime.now()
            if server_status.get('next_check'):
                next_check = datetime.fromisoformat(server_status['next_check'])
                if current_time >= next_check:
                    statuses = ['offline', 'starting', 'running']
                    weights = [0.3, 0.2, 0.5]
                    new_status = random.choices(statuses, weights=weights)[0]
                    update_server_status(new_status)
            if server_status['auto_check_active'] and not server_status.get('next_check'):
                interval_minutes = server_status['check_interval_minutes']
                next_check_time = current_time + timedelta(minutes=interval_minutes)
                server_status['next_check'] = next_check_time.isoformat()
                save_status()
        time.sleep(10)

@app.route("/")
def dashboard():
    return render_template("dashboard.html", status=server_status)

@app.route("/api/status")
def api_status():
    return jsonify(server_status)

@app.route("/api/start", methods=["POST"])
def start_server():
    update_server_status('starting')
    server_status['last_action'] = 'start'
    save_status()
    def simulate():
        time.sleep(10)
        update_server_status('running')
    threading.Thread(target=simulate, daemon=True).start()
    return jsonify({'success': True, 'message': 'در حال راه‌اندازی سرور...'})

@app.route("/api/stop", methods=["POST"])
def stop_server():
    update_server_status('offline')
    server_status['last_action'] = 'stop'
    save_status()
    return jsonify({'success': True, 'message': 'سرور خاموش شد'})

@app.route("/api/toggle_auto_check", methods=["POST"])
def toggle_auto_check():
    data = request.json or {}
    server_status['auto_check_active'] = bool(data.get('active', True))
    if server_status['auto_check_active']:
        interval_minutes = server_status['check_interval_minutes']
        next_check_time = datetime.now() + timedelta(minutes=interval_minutes)
        server_status['next_check'] = next_check_time.isoformat()
    else:
        server_status['next_check'] = None
    save_status()
    return jsonify({'success': True, 'message': f'بررسی خودکار {"فعال" if server_status["auto_check_active"] else "غیرفعال"} شد'})

@app.route("/api/set_check_interval", methods=["POST"])
def set_check_interval():
    data = request.json or {}
    min_minutes = int(data.get('min', 1))
    max_minutes = int(data.get('max', 5))
    server_status['check_interval_minutes'] = random.uniform(min_minutes, max_minutes)
    if server_status['auto_check_active']:
        next_check_time = datetime.now() + timedelta(minutes=server_status['check_interval_minutes'])
        server_status['next_check'] = next_check_time.isoformat()
    save_status()
    return jsonify({'success': True, 'message': f'فاصله زمانی بررسی تنظیم شد: {min_minutes}-{max_minutes} دقیقه'})

def main():
    load_status()
    threading.Thread(target=auto_check_thread, daemon=True).start()
    port = int(os.environ.get("PORT", "5000"))
    app.run(debug=False, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
