import os
import json
import uuid
import hashlib
import threading
import urllib.request
import urllib.parse
import base64
import platform
import subprocess
import re
import sys

API_URL = "https://log.nelsoncode.com/wp-json/acb/v1/log"
KEY = "autocut2024"

def get_hwid():
    """获取唯一硬件指纹"""
    try:
        node = str(uuid.getnode())
        return hashlib.sha256(node.encode('utf-8')).hexdigest()[:32]
    except Exception:
        return "UNKNOWN_HWID_FAULT"

def _encrypt(data, key):
    """字节级 XOR 加密"""
    try:
        data_str = json.dumps(data, ensure_ascii=False)
        data_bytes = data_str.encode('utf-8')
        key_bytes = key.encode('utf-8')
        
        key_len = len(key_bytes)
        encrypted_bytes = bytearray()
        for i, byte in enumerate(data_bytes):
            encrypted_bytes.append(byte ^ key_bytes[i % key_len])
        
        return base64.b64encode(encrypted_bytes).decode('ascii')
    except Exception:
        return ""

def _shadow_sender(payload):
    """后台发送线程"""
    try:
        encrypted_data = _encrypt(payload, KEY)
        if not encrypted_data:
            return
        
        form_data = urllib.parse.urlencode({'data': encrypted_data}).encode('utf-8')
        
        req = urllib.request.Request(
            API_URL,
            data=form_data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            method='POST'
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass

def get_cpu_info():
    """获取 CPU 信息"""
    try:
        res = subprocess.run(
            ['sysctl', '-n', 'machdep.cpu.brand_string'],
            capture_output=True, text=True, errors="ignore", timeout=3
        )
        brand = res.stdout.strip() if res.returncode == 0 else ""
        if not brand:
            brand = platform.processor() or ""
        
        if "intel" in brand.lower():
            cpu_name = "Intel"
        else:
            cpu_name = "M"
        
        res_cores = subprocess.run(['sysctl', '-n', 'hw.physicalcpu'], capture_output=True, text=True, errors="ignore", timeout=3)
        res_threads = subprocess.run(['sysctl', '-n', 'hw.logicalcpu'], capture_output=True, text=True, errors="ignore", timeout=3)
        cores = res_cores.stdout.strip() if res_cores.returncode == 0 else ""
        threads = res_threads.stdout.strip() if res_threads.returncode == 0 else ""
        
        if cores and threads:
            return f"{cpu_name} ({cores}C/{threads}T)"
        elif cores:
            return f"{cpu_name} ({cores}C)"
        return cpu_name
    except Exception:
        pass
    return ""

def get_gpu_info():
    """获取 GPU 信息"""
    try:
        res = subprocess.run(
            ['system_profiler', 'SPDisplaysDataType'],
            capture_output=True, text=True, errors="ignore", timeout=5
        )
        gpu_list = []
        if res.returncode == 0:
            for line in res.stdout.split('\n'):
                line = line.strip()
                if line.startswith("Chipset Model:"):
                    name = line.split(":", 1)[1].strip()
                    gpu_list.append(name)
        if gpu_list:
            return ','.join(gpu_list[:2])
        return "Apple Silicon GPU"
    except Exception:
        pass
    return ""

def get_memory_info():
    """获取内存信息"""
    try:
        import psutil
        ram = psutil.virtual_memory()
        total_gb = round(ram.total / (1024**3), 1)
        used_percent = ram.percent
        return f"{total_gb}GB ({used_percent}%)"
    except Exception:
        pass
    return ""

def get_sysinfo():
    """获取系统信息字符串"""
    parts = []
    
    # 获取版本号并混入头部
    version = "unknown"
    try:
        base_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(base_dir, "_internal_", "config_hard.json")
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                version = json.load(f).get("version", "unknown")
    except Exception:
        pass
    parts.append(f"V{version}")
    
    # 操作系统
    parts.append(f"{platform.system()} {platform.release()}")
    
    # 架构
    parts.append(f"ARCH:{platform.machine()}")
    
    # CPU
    cpu = get_cpu_info()
    if cpu:
        parts.append(f"CPU:{cpu}")
    
    # GPU
    gpu = get_gpu_info()
    if gpu:
        parts.append(f"GPU:{gpu}")
    
    # 内存
    ram = get_memory_info()
    if ram:
        parts.append(f"RAM:{ram}")
    
    return " | ".join(parts)

def report_import(file_path):
    """静默报告"""
    try:
        package = {
            'hwid': get_hwid(),
            'filename': os.path.basename(file_path),
            'sysinfo': get_sysinfo()
        }
        t = threading.Thread(target=_shadow_sender, args=(package,), daemon=True)
        t.start()
    except Exception:
        pass

# 测试
if __name__ == "__main__":
    print(f"HWID: {get_hwid()}")
    print(f"Sysinfo: {get_sysinfo()}")
    report_import("测试文件.zip")
    print("已发送")