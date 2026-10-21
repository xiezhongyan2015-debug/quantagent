"""Bridge current-user DPAPI to approved network worker using ephemeral RSA.

The private key exists only in this process. Disk receives a public key and an
OAEP ciphertext, never a password, password hash or private key.
"""
import json
import time
import uuid
from pathlib import Path
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa,padding
import probe_password_reauth as probe

def main():
    root=probe.DEST
    if (root/'password_submission.json').exists():raise RuntimeError('Already attempted; no retry')
    key=rsa.generate_private_key(public_exponent=65537,key_size=3072)
    nonce=uuid.uuid4().hex
    request={'nonce':nonce,'public_key':key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()}
    request_path=root/'credential_transport_request.json'
    with request_path.open('x',encoding='utf-8') as f:json.dump(request,f)
    print('Waiting for encrypted credential transport; private key remains in memory',flush=True)
    deadline=time.monotonic()+120
    envelope=root/('credential_transport_'+nonce+'.bin')
    while not envelope.exists():
        if time.monotonic()>deadline:raise RuntimeError('Credential transport timed out')
        time.sleep(.5)
    clear=key.decrypt(envelope.read_bytes(),padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=nonce.encode()))
    credentials=json.loads(clear);del clear,key
    try:probe.main(lambda:credentials)
    finally:credentials.clear()

if __name__=='__main__':main()
