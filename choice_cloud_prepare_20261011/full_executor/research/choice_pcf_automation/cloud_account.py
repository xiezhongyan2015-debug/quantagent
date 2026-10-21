"""Confirmed captain identity, without passwords or session tokens."""
from pathlib import Path
import hashlib
import json
from verify_readonly import ROOT
ACCOUNT='000000000000000000'
ACCOUNT_SHA256='0000000000000000000000000000000000000000000000000000000000000000'
UID_SHA256='0000000000000000000000000000000000000000000000000000000000000000'
DIRECTORIES=[ROOT/'research/choice_pcf_automation/cloud_ledger']
def assert_binding(identity):
    if hashlib.sha256(ACCOUNT.encode()).hexdigest()!=ACCOUNT_SHA256 or identity.get('account_sha256')!=ACCOUNT_SHA256 or identity.get('uid_sha256')!=UID_SHA256:
        raise RuntimeError('Captain identity binding differs')

def verify_deployment():
    manifest=json.loads((ROOT/'deployment_manifest.json').read_text(encoding='utf-8'))
    for name,expected in manifest['files'].items():
        path=(ROOT/name).resolve()
        if not path.is_relative_to(ROOT.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise RuntimeError('Executor or pinned source evidence changed; preparation must be verified again')
