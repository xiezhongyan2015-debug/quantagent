"""One-time local secret enrollment through a non-echoing terminal prompt."""
import getpass
import json
import os
import win32crypt
from choice_auth import AUTH_ROOT

def main():
    identity=json.loads((AUTH_ROOT/'identity.json').read_text(encoding='utf-8'))
    path=AUTH_ROOT/'credentials.dpapi'
    if path.exists():raise RuntimeError('Credential vault already exists; no silent replacement')
    password=getpass.getpass('Choice password (not echoed): ')
    if not password:raise RuntimeError('Empty password not stored')
    payload=json.dumps(dict(username=identity['username'],password=password),ensure_ascii=False).encode('utf-8')
    blob=win32crypt.CryptProtectData(payload,'Choice competition login; current Windows user only',None,None,None,0)
    with path.open('xb') as f:f.write(blob);f.flush();os.fsync(f.fileno())
    del password,payload
    print('Encrypted credential stored with Windows current-user DPAPI; no plaintext file created.')

if __name__=='__main__':main()
