import os
import json
import base64
import uuid
import time
import hashlib
from datetime import datetime
from urllib import request as url_request
from urllib.error import HTTPError
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding

PUB_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAtd5L3KE/mspyeTjt689P
ceaVMEOhbZvy6tDrj5wR/V+wnECqhKcEMYQYrm76FUUgLKKulloYYBzIbtDT0t+2
JWpBdTAYm36ao4grvcnWTvOjtFzSy3mvXu0hIuTUNE5OlNGiVYOIG+yLFZijm4X9
Arp71rK1XfUOu+WMNxVn5iBGQZeaR3XxRpcUGIR6r93uB5XXDGUifBpW49zD5nJt
V0WTCjnjWeweArb4jj7VD4/8gzYhL7ZgB9cO3wCH6Ba9NpVLf1+n29S1BoNDqMfQ
LFcrUpMs0lVQ4bhhHz1dfcKLqxj84rjvfc4A4yD6lYBB4YgsFxyALR1dlT6W3/aP
xQIDAQAB
-----END PUBLIC KEY-----"""

API_URL = "https://abc.nelsoncode.com/wp-json/autocut/v5/auth"
def _get_cache_path():
    # Attempt to locate autocut logic for base_dir/frozen state, or fallback to file dir
    try:
        import acut_engine as autocut
        return os.path.join(autocut.INTERNAL_DIR, "offline_license.json")
    except:
        _internal = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_internal_")
        os.makedirs(_internal, exist_ok=True)
        return os.path.join(_internal, "offline_license.json")

OFFLINE_CACHE = _get_cache_path()

def get_hwid():
    """获取硬件唯一指纹 (HWID) - 工业级稳定性增强"""
    node = str(uuid.getnode())
    # 使用 SHA256 确保长度符合 16-64 位协议要求，且具备抗扰动性
    return hashlib.sha256(node.encode('utf-8')).hexdigest()[:32]

def _verify_v5(res, sig_b64, hwid):
    """
    [PROTOCOL v5.0] 物理验签逻辑
    按严格工业规格重建原始载荷字符串：state|hwid|timestamp
    """
    raw_payload = f"{res.get('state')}|{hwid}|{res.get('timestamp')}"
    key = serialization.load_pem_public_key(PUB_KEY.encode('utf-8'))
    try:
        key.verify(
            base64.b64decode(sig_b64),
            raw_payload.encode('utf-8'),
            padding.PKCS1v15(),
            hashes.SHA256()
        )
        return True
    except Exception as e:
        return False

_cache_authorized = False
_cache_msg = "未验证"
_cache_state = "IDLE"
_cache_time = 0

def verify_license(cd_key=None):
    """
    [Audit-Ready] 工业授权流：极致精简，高压容错
    """
    global _cache_authorized, _cache_msg, _cache_state, _cache_time
    
    now = time.time()
    if not cd_key and _cache_authorized and (now - _cache_time < 3600):
        return True, _cache_msg, _cache_state, False

    hwid = get_hwid()
    
    try:
        req_data = {"hwid": hwid}
        if cd_key:
            req_data["cd_key"] = cd_key
            
        data_bytes = json.dumps(req_data).encode('utf-8')
        
        req = url_request.Request(
            API_URL, 
            data=data_bytes, 
            method='POST',
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Safari/537.36',
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
        )
        
        try:
            with url_request.urlopen(req, timeout=10) as response:
                resp_bytes = response.read()
                if not resp_bytes:
                    return False, "服务器返回空响应 (Empty Response)", "SERVER_EMPTY", True
                
                resp_str = resp_bytes.decode('utf-8')
                try:
                    res = json.loads(resp_str)
                except json.JSONDecodeError:
                    snippet = resp_str[:50].replace('\n', ' ').strip()
                    return False, f"服务器响应格式错误 (Invalid JSON): {snippet}", "JSON_ERR", True
        except HTTPError as he:
            if he.code == 403:
                try:
                    reason_raw = he.read().decode('utf-8', errors='ignore').strip()
                    try:
                        reason_obj = json.loads(reason_raw)
                        reason = reason_obj.get('message') or reason_obj.get('reason') or reason_raw
                    except:
                        reason = reason_raw or "访问被服务器拒绝"
                except:
                    reason = "访问被服务器拒绝"
                return False, f"授权阻断: {reason}", "BLOCKED", True
            return False, f"API 拒绝 ({he.code})", "SERVER_REJECT", True
        
        sig_b64 = res.get('signature')
        if sig_b64 and _verify_v5(res, sig_b64, hwid):
            _cache_authorized, _cache_time = True, now
            state = res.get('state')
            _cache_state = state
            
            # 增强型 UI 映射：避免误导“永久”字样
            expire_str = res.get('expire_date', 'Unknown')
            if state == 'SUCCESS':
                _cache_msg = f"授权验证通过 (商业版 - 有效至: {expire_str})"
            elif state == 'TRIAL':
                _cache_msg = f"授权验证通过 (试用版 - 剩余 {res.get('days_left', 0)} 天)"
            elif state == 'EXPIRED':
                _cache_msg = f"授权已过期 ({expire_str})"
                return False, _cache_msg, "EXPIRED", True
            else:
                _cache_msg = "授权验证通过 (Online)"
            
            try:
                with open(OFFLINE_CACHE, 'w', encoding='utf-8') as f: 
                    json.dump(res, f)
            except: pass
            
            return True, _cache_msg, _cache_state, True
        else:
            return False, "签名校验失败 (Protocol Mismatch)", "SIG_ERR", True
            
    except Exception as e:
        err_type = type(e).__name__
        err_msg = str(e)
        if os.path.exists(OFFLINE_CACHE):
            try:
                with open(OFFLINE_CACHE, 'r', encoding='utf-8') as f:
                    res = json.load(f)
                cached_sig = res.get('signature')
                if cached_sig and _verify_v5(res, cached_sig, hwid):
                    _cache_authorized, _cache_time = True, now
                    
                state = res.get('state')
                expire_date_str = res.get('expire_date')
                
                # --- 离线过期核心防线 ---
                if expire_date_str:
                    try:
                        expire_dt = datetime.strptime(expire_date_str, "%Y-%m-%d %H:%M:%S")
                        if datetime.now() > expire_dt:
                            _cache_authorized = False
                            _cache_msg = f"授权已于 {expire_date_str} 过期"
                            return False, _cache_msg, "EXPIRED", False
                    except: pass
                
                if state == 'SUCCESS':
                    _cache_msg = f"授权验证通过 (商业版 - 离线模式)"
                elif state == 'TRIAL':
                    _cache_msg = f"授权验证通过 (试用版 - 离线模式)"
                elif state == 'EXPIRED':
                    _cache_msg = "授权已过期"
                    return False, _cache_msg, "EXPIRED", False
                    
                _cache_state = state
                return True, _cache_msg, _cache_state, False
            except: pass
        
        return False, f"网络故障 ({err_type}): {err_msg}", "NETWORK_ERR", False

def verify_license_offline():
    """
    [Offline-Only] 仅读取本地缓存证书进行鉴权，不发起任何网络请求。
    用于合成前/面板展示等高频场景的快速鉴权。
    返回: (is_ok, msg, state)
    """
    global _cache_authorized, _cache_msg, _cache_state, _cache_time

    now = time.time()
    # 内存缓存命中（与在线验证共享同一缓存）
    if _cache_authorized and (now - _cache_time < 600):
        return True, _cache_msg, _cache_state

    hwid = get_hwid()

    if not os.path.exists(OFFLINE_CACHE):
        return False, "未找到本地授权证书，请联网验证一次", "NO_CERT"

    try:
        with open(OFFLINE_CACHE, 'r', encoding='utf-8') as f:
            res = json.load(f)

        cached_sig = res.get('signature')
        if not cached_sig or not _verify_v5(res, cached_sig, hwid):
            return False, "本地证书签名无效", "SIG_ERR"

        state = res.get('state')
        expire_date_str = res.get('expire_date')

        # 离线过期核心防线
        if expire_date_str:
            try:
                expire_dt = datetime.strptime(expire_date_str, "%Y-%m-%d %H:%M:%S")
                if datetime.now() > expire_dt:
                    _cache_authorized = False
                    _cache_msg = f"授权已于 {expire_date_str} 过期"
                    _cache_state = "EXPIRED"
                    return False, _cache_msg, "EXPIRED"
            except: pass

        if state == 'EXPIRED':
            _cache_authorized = False
            return False, "授权已过期", "EXPIRED"

        if state == 'SUCCESS':
            _cache_msg = "授权验证通过 (商业版 - 离线模式)"
        elif state == 'TRIAL':
            _cache_msg = "授权验证通过 (试用版 - 离线模式)"
        else:
            _cache_msg = "授权验证通过 (离线)"

        _cache_authorized = True
        _cache_time = now
        _cache_state = state
        return True, _cache_msg, _cache_state

    except Exception as e:
        return False, f"证书读取失败: {type(e).__name__}", "CERT_ERR"

if __name__ == "__main__":
    hwid = get_hwid()
    # verify_license() logic is now strictly internal and silent
