"""Read only the operator-bound Choice profile; secrets remain in memory."""
import argparse
import base64
import ctypes
from ctypes import wintypes
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker, BASE


def session_tokens(names=('JToken',), profile=None):
    if not names or set(names) - {'JToken','utoken','ctoken','UID'}:
        raise ValueError('Session field outside explicit authorization')
    profile = Path(profile or binding()['choice_profile']).expanduser().resolve()
    prefs = json.loads((profile / 'LocalPrefs.json').read_text(encoding='utf-8'))
    wrapped = base64.b64decode(prefs['os_crypt']['encrypted_key'])
    if not wrapped.startswith(b'DPAPI'):
        raise ValueError('Unsupported session key format')
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = (ctypes.c_ubyte * len(wrapped[5:])).from_buffer_copy(wrapped[5:])
    source = Blob(len(buffer), buffer)
    target = Blob()
    unprotect = ctypes.windll.crypt32.CryptUnprotectData
    unprotect.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    unprotect.restype = wintypes.BOOL
    if not unprotect(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)):
        raise ValueError('Current-user session decryption unavailable')
    try:
        key = ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(target.data)
    result = {}
    with sqlite3.connect((profile / 'Network/Cookies').as_uri()+'?mode=ro', uri=True, timeout=1) as db:
        for name in names:
            rows = db.execute("SELECT value, encrypted_value FROM cookies WHERE host_key=? AND name=?",
                              ('choicelab.eastmoney.com', name)).fetchall()
            if len(rows) != 1:
                raise ValueError('Expected one current Choice session field')
            value, encrypted = rows[0]
            if not value:
                if encrypted[:3] != b'v10':
                    raise ValueError('Unsupported cookie format')
                plain = AESGCM(key).decrypt(encrypted[3:15], encrypted[15:], None)
                host_digest = hashlib.sha256(b'choicelab.eastmoney.com').digest()
                if plain.startswith(host_digest):
                    plain = plain[len(host_digest):]
                value = plain.decode('utf-8')
            if not value:
                raise ValueError('Empty authorized session field')
            result[name] = value
    return result


def session_token():
    return session_tokens()['JToken']


class GetOnlySession(requests.Session):
    KEYS = {
        'AguTrade/SpoBalInfo': {'accId'},
        'AguTrade/SpoHold': {'accId', 'ssid', 'dvid', 'appver', 'fToken', 'uToken'},
        'AguTrade/SpoOrders': {'accId', 'dataRange', 'pageNo', 'pageSize'},
        'AguTrade/SpoDeal': {'accId', 'dataRange', 'pageNo', 'pageSize'},
    }
    def __init__(self, account):
        super().__init__()
        self.account = account
        self.audit = []

    def request(self, method, url, **kwargs):
        endpoint = url[len(BASE):] if url.startswith(BASE) else ''
        params = kwargs.get('params', {})
        if (method.upper() != 'GET' or endpoint not in self.KEYS or
                set(params) != self.KEYS[endpoint] or params['accId'] != self.account or
                kwargs.get('allow_redirects') is not False or kwargs.get('json') is not None or
                kwargs.get('data') is not None):
            raise RuntimeError('Request blocked before network')
        start = time.perf_counter()
        response = super().request(method, url, **kwargs)
        self.audit.append({'endpoint':endpoint,'method':'GET','http_status':response.status_code,
                           'elapsed_ms':round((time.perf_counter()-start)*1000,3)})
        return response



APP_DIR = Path(__file__).resolve().parent / 'runtime'

def binding(required=True):
    path = APP_DIR / 'account.json'
    if not path.exists():
        if required:
            raise RuntimeError('请先运行 setup_account.py 初始化自己的模拟账户')
        return {}
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if (value.get('account_mode') != 'competition_simulation_only'
            or not isinstance(value.get('account_id'), str) or not value['account_id'].strip()
            or not isinstance(value.get('uid_sha256'), str) or len(value['uid_sha256']) != 64
            or not value.get('choice_profile')):
        raise RuntimeError('本机模拟账户绑定文件无效')
    return value

def account_id(required=True):
    return binding(required).get('account_id', '')
