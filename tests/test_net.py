"""HTTPS downloads must not depend on where the build machine keeps its CAs.

Standalone, like the other suites here; puts this checkout's src/ on the path.
Runs itself again with the system CA locations hidden (what a frozen build
sees on a machine laid out differently from the build host).
"""
import os
import subprocess
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / 'src')

if os.environ.get('_NET_TEST_CHILD') != '1':
    env = dict(os.environ, _NET_TEST_CHILD='1',
               SSL_CERT_FILE='/nonexistent/ca.pem', SSL_CERT_DIR='/nonexistent')
    sys.exit(subprocess.call([sys.executable, __file__], env=env))

sys.path.insert(0, SRC)
import ssl
from subtitld.modules.net import https_context

fails = []
bare = ssl.create_default_context().cert_store_stats()['x509_ca']
ours = https_context().cert_store_stats()['x509_ca']
print(f'CAs with the system paths hidden: default context {bare}, https_context {ours}')
if bare:
    print('note: this Python still found system CAs; the check below still applies')
if ours < 100:
    fails.append('https_context has no CA bundle of its own')
if https_context() is not https_context():
    fails.append('context is rebuilt on every call')

# Every urllib download in the host goes through it.
for rel in ('subtitld/modules/addons/installer.py',
            'subtitld/modules/addons/builtin/subtitld_cloud_shared.py'):
    text = (Path(SRC) / rel).read_text()
    if text.count('context=https_context()') < text.count('urlopen(req'):
        fails.append(f'{rel}: a urlopen call without https_context')

print('FAIL: ' + ', '.join(fails) if fails else 'NET TESTS PASS')
sys.exit(1 if fails else 0)
