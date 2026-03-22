#!/usr/bin/env python3
import psutil
import json
import os
import subprocess
import time
import sqlite3
import datetime
import threading
import hashlib
import secrets
from flask import Flask, jsonify, send_from_directory, request, render_template, redirect, url_for, session
from flask_cors import CORS
from functools import wraps

app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = secrets.token_hex(32)
CORS(app)

# Конфигурация
DATA_DIR = os.path.expanduser('~/server-stats/data')
DB_PATH = os.path.join(DATA_DIR, 'stats.db')
os.makedirs(DATA_DIR, exist_ok=True)

# Пароль для админки (смени на свой!)
ADMIN_PASSWORD_HASH = hashlib.sha256('admin'.encode()).hexdigest()


# Декоратор для проверки авторизации
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorated_function


# Инициализация БД
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS stats
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp REAL,
                  cpu_percent REAL,
                  cpu_temp REAL,
                  memory_percent REAL,
                  net_sent REAL,
                  net_recv REAL)''')
    conn.commit()
    conn.close()


# Функция сбора и сохранения данных
def collect_and_save():
    while True:
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()

            cpu_percent = psutil.cpu_percent(interval=1)

            temps = psutil.sensors_temperatures()
            cpu_temp = None
            if 'coretemp' in temps:
                cpu_temp = temps['coretemp'][0].current
            elif 'k10temp' in temps:
                cpu_temp = temps['k10temp'][0].current

            memory = psutil.virtual_memory()
            net_io = psutil.net_io_counters()

            c.execute('''INSERT INTO stats (timestamp, cpu_percent, cpu_temp, memory_percent, net_sent, net_recv)
                         VALUES (?, ?, ?, ?, ?, ?)''',
                      (time.time(), cpu_percent, cpu_temp, memory.percent, net_io.bytes_sent, net_io.bytes_recv))
            conn.commit()
            conn.close()

            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('DELETE FROM stats WHERE timestamp < ?', (time.time() - 86400,))
            conn.commit()
            conn.close()

        except Exception as e:
            print(f"Ошибка сбора данных: {e}")

        time.sleep(10)


# Запуск фонового сбора
threading.Thread(target=collect_and_save, daemon=True).start()
init_db()


# ==================== API ЭНДПОИНТЫ ====================

@app.route('/api/stats')
def get_stats():
    """Текущая статистика"""
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        cpu_percent_per_core = psutil.cpu_percent(interval=1, percpu=True)

        temps = psutil.sensors_temperatures()
        cpu_temp = None
        if 'coretemp' in temps:
            cpu_temp = temps['coretemp'][0].current
        elif 'k10temp' in temps:
            cpu_temp = temps['k10temp'][0].current

        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()

        boot_time = psutil.boot_time()
        uptime_seconds = time.time() - boot_time

        net_io = psutil.net_io_counters()

        disks = []
        for partition in psutil.disk_partitions():
            if 'loop' not in partition.device and 'snap' not in partition.mountpoint:
                try:
                    usage = psutil.disk_usage(partition.mountpoint)
                    disks.append({
                        'device': partition.device,
                        'mount': partition.mountpoint,
                        'total': usage.total,
                        'used': usage.used,
                        'free': usage.free,
                        'percent': usage.percent
                    })
                except:
                    pass

        battery = psutil.sensors_battery()
        battery_info = None
        if battery:
            battery_info = {
                'percent': battery.percent,
                'plugged': battery.power_plugged,
                'seconds_left': battery.secsleft if battery.secsleft != psutil.POWER_TIME_UNLIMITED else None
            }

        system_info = {
            'hostname': os.uname().nodename,
            'kernel': os.uname().release,
            'os': os.uname().sysname,
            'cpu_model': subprocess.getoutput(
                "cat /proc/cpuinfo | grep 'model name' | head -1 | cut -d':' -f2").strip(),
            'cpu_cores': psutil.cpu_count(),
            'total_ram': memory.total
        }

        return jsonify({
            'cpu': {
                'percent': cpu_percent,
                'percent_per_core': cpu_percent_per_core,
                'temperature': cpu_temp,
                'cores': psutil.cpu_count()
            },
            'memory': {
                'total': memory.total,
                'available': memory.available,
                'percent': memory.percent,
                'used': memory.used,
                'free': memory.free,
                'swap_total': swap.total,
                'swap_used': swap.used,
                'swap_percent': swap.percent
            },
            'disks': disks,
            'uptime': {
                'seconds': uptime_seconds,
                'days': uptime_seconds // 86400,
                'hours': (uptime_seconds % 86400) // 3600,
                'minutes': (uptime_seconds % 3600) // 60
            },
            'network': {
                'sent': net_io.bytes_sent,
                'recv': net_io.bytes_recv
            },
            'battery': battery_info,
            'system': system_info
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/history')
def get_history():
    """Исторические данные для графиков"""
    hours = request.args.get('hours', 6, type=int)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT timestamp, cpu_percent, cpu_temp, memory_percent, net_sent, net_recv
                 FROM stats WHERE timestamp > ? ORDER BY timestamp''',
              (time.time() - hours * 3600,))
    rows = c.fetchall()
    conn.close()

    data = []
    for row in rows:
        data.append({
            'timestamp': row[0],
            'cpu_percent': row[1],
            'cpu_temp': row[2],
            'memory_percent': row[3],
            'net_sent': row[4],
            'net_recv': row[5]
        })
    return jsonify(data)


@app.route('/api/services')
def get_services():
    """Список системных служб"""
    services = []
    important_services = ['sshd', 'nginx', 'httpd', 'docker', 'cron', 'NetworkManager']
    for svc in important_services:
        status = subprocess.getoutput(f'systemctl is-active {svc}').strip()
        if status in ['active', 'inactive', 'failed', 'unknown']:
            services.append({
                'name': svc,
                'status': status,
                'description': subprocess.getoutput(f'systemctl show {svc} --property=Description --value').strip()
            })
    return jsonify(services)


@app.route('/api/service/<name>/<action>', methods=['POST'])
def service_action(name, action):
    """Управление службой"""
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401

    token = request.headers.get('X-Admin-Token')
    if token != session.get('token'):
        return jsonify({'error': 'Invalid token'}), 401

    if action not in ['start', 'stop', 'restart']:
        return jsonify({'error': 'Invalid action'}), 400

    result = subprocess.run(f'sudo systemctl {action} {name}', shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        return jsonify({'success': True})
    else:
        return jsonify({'error': result.stderr}), 500


@app.route('/api/logs')
def get_logs():
    """Получение системных логов"""
    lines = request.args.get('lines', 50, type=int)
    unit = request.args.get('unit', 'system')

    if unit == 'system':
        cmd = f'journalctl -n {lines} --no-pager'
    else:
        cmd = f'journalctl -u {unit} -n {lines} --no-pager'

    logs = subprocess.getoutput(cmd)
    return jsonify({'logs': logs.split('\n')})


@app.route('/api/network/connections')
def get_network_connections():
    """Активные сетевые подключения"""
    result = subprocess.getoutput("ss -tun | tail -n +2")
    connections = []
    for line in result.split('\n'):
        if line:
            parts = line.split()
            if len(parts) >= 5:
                connections.append({
                    'netid': parts[0],
                    'state': parts[1] if len(parts) > 1 else '',
                    'recv_q': parts[2],
                    'send_q': parts[3],
                    'local': parts[4],
                    'peer': parts[5]
                })
    return jsonify(connections[:50])


@app.route('/api/system/info')
def get_system_info():
    """Детальная системная информация"""
    info = {
        'cpu_model': subprocess.getoutput("cat /proc/cpuinfo | grep 'model name' | head -1 | cut -d':' -f2").strip(),
        'cpu_cores': psutil.cpu_count(),
        'cpu_freq': psutil.cpu_freq().current if psutil.cpu_freq() else None,
        'total_ram': psutil.virtual_memory().total,
        'swap_total': psutil.swap_memory().total,
        'kernel': os.uname().release,
        'hostname': os.uname().nodename,
        'uptime': time.time() - psutil.boot_time(),
        'last_boot': datetime.datetime.fromtimestamp(psutil.boot_time()).strftime('%Y-%m-%d %H:%M:%S')
    }
    return jsonify(info)


@app.route('/api/terminal', methods=['POST'])
def terminal():
    """Выполнение команды в терминале"""
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.json
    cmd = data.get('cmd', '')
    if not cmd:
        return jsonify({'error': 'No command'}), 400

    dangerous = ['rm -rf /', 'dd if=', 'mkfs', ':(){ :|:& };:', 'chmod 777 /']
    for d in dangerous:
        if d in cmd:
            return jsonify({'error': 'Command blocked for security'}), 403

    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
    return jsonify({
        'output': result.stdout + result.stderr,
        'returncode': result.returncode
    })


@app.route('/api/files/list')
def list_files():
    """Список файлов в директории"""
    path = request.args.get('path', os.path.expanduser('~'))
    if not os.path.exists(path):
        return jsonify({'error': 'Path not found'}), 404

    files = []
    try:
        for item in os.listdir(path):
            full_path = os.path.join(path, item)
            stat = os.stat(full_path)
            files.append({
                'name': item,
                'path': full_path,
                'is_dir': os.path.isdir(full_path),
                'size': stat.st_size if os.path.isfile(full_path) else 0,
                'modified': datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
            })
        files.sort(key=lambda x: (not x['is_dir'], x['name']))
    except PermissionError:
        return jsonify({'error': 'Permission denied'}), 403

    return jsonify({'path': path, 'files': files})


@app.route('/api/files/read')
def read_file():
    """Чтение содержимого файла"""
    path = request.args.get('path')
    if not path or not os.path.isfile(path):
        return jsonify({'error': 'Invalid file'}), 400

    try:
        with open(path, 'r') as f:
            content = f.read(50000)
        return jsonify({'content': content, 'path': path})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/files/save', methods=['POST'])
def save_file():
    """Сохранение файла"""
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.json
    path = data.get('path')
    content = data.get('content', '')

    if not path:
        return jsonify({'error': 'No path'}), 400

    try:
        with open(path, 'w') as f:
            f.write(content)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== СТРАНИЦЫ ====================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password')
        if hashlib.sha256(password.encode()).hexdigest() == ADMIN_PASSWORD_HASH:
            session['logged_in'] = True
            session['token'] = secrets.token_hex(32)
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error='Неверный пароль')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/')
def dashboard():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template('index.html')


if __name__ == '__main__':
    print("🚀 Запуск полной панели управления сервером...")
    print("🔐 Пароль по умолчанию: admin")
    print("📊 Открой в браузере: http://localhost:5000")
    app.run(host='0.0.0.0', port=5000, debug=False)