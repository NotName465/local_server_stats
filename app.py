#!/usr/bin/env python3
import psutil
import json
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
import os
import subprocess
import time

app = Flask(__name__, static_folder='.')
CORS(app)  # Разрешаем запросы с любого источника


def get_cpu_temperature():
    """Пытается получить температуру CPU"""
    temps = psutil.sensors_temperatures()
    if 'coretemp' in temps:
        # Intel
        return temps['coretemp'][0].current
    elif 'k10temp' in temps:
        # AMD
        return temps['k10temp'][0].current
    elif 'cpu_thermal' in temps:
        # ARM
        return temps['cpu_thermal'][0].current
    else:
        # Пробуем через системную команду
        try:
            result = subprocess.run(['sensors'], capture_output=True, text=True)
            for line in result.stdout.split('\n'):
                if 'Package id 0:' in line or 'Core 0:' in line or 'temp1:' in line:
                    import re
                    temps = re.findall(r'\+(\d+\.\d+)°C', line)
                    if temps:
                        return float(temps[0])
        except:
            pass
        return None


def get_network_io():
    """Собирает сетевую статистику"""
    net_io = psutil.net_io_counters()
    return {
        'bytes_sent': net_io.bytes_sent,
        'bytes_recv': net_io.bytes_recv
    }


def get_disk_usage():
    """Информация о дисках"""
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
    return disks


@app.route('/api/stats')
def get_stats():
    """Основной API эндпоинт"""
    try:
        # CPU
        cpu_percent = psutil.cpu_percent(interval=1)
        cpu_percent_per_core = psutil.cpu_percent(interval=1, percpu=True)

        # Memory
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()

        # Temperature
        temp = get_cpu_temperature()

        # Uptime
        boot_time = psutil.boot_time()
        uptime_seconds = time.time() - boot_time
        uptime_days = uptime_seconds // 86400
        uptime_hours = (uptime_seconds % 86400) // 3600
        uptime_minutes = (uptime_seconds % 3600) // 60

        # Network
        net_io = get_network_io()

        # Disks
        disks = get_disk_usage()

        # Battery (если есть)
        battery = psutil.sensors_battery()
        battery_info = None
        if battery:
            battery_info = {
                'percent': battery.percent,
                'plugged': battery.power_plugged,
                'seconds_left': battery.secsleft if battery.secsleft != psutil.POWER_TIME_UNLIMITED else None
            }

        return jsonify({
            'cpu': {
                'percent': cpu_percent,
                'percent_per_core': cpu_percent_per_core,
                'temperature': temp,
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
                'days': uptime_days,
                'hours': uptime_hours,
                'minutes': uptime_minutes,
                'seconds': uptime_seconds
            },
            'network': {
                'sent': net_io['bytes_sent'],
                'recv': net_io['bytes_recv']
            },
            'battery': battery_info
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/')
def index():
    """Отдает HTML страницу"""
    return send_from_directory('.', 'index.html')


@app.route('/<path:path>')
def static_files(path):
    """Отдает статические файлы"""
    return send_from_directory('.', path)


if __name__ == '__main__':
    print("🚀 Сервер статистики запускается...")
    print("📊 Открой в браузере: http://localhost:5000")
    app.run(host='0.0.0.0', port=5000, debug=False)