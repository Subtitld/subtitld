"""Live co-editing in a package: run with the package's own Python (snap run --shell, flatpak run --command).

Live co-editing's libraries load only once a project is shared, after the start-up smoke test, so check them
here: Qt's websockets (the link to subtitld.cc), and libdatachannel (media between collaborators) carrying 4 MB
between two connections in this process.
"""
import hashlib
import os
import sys
import threading

from PySide6.QtCore import QCoreApplication
from PySide6.QtWebSockets import QWebSocket

from subtitld.modules import rtc

app = QCoreApplication(sys.argv)
QWebSocket()
print('QtWebSockets: ok')

lib = rtc.library()
if lib is None:
    sys.exit('libdatachannel: not found')
print('libdatachannel:', lib._name)

offerer, answerer = rtc.PeerConnection(), rtc.PeerConnection()
offerer.on_description = lambda sdp, kind: answerer.set_remote_description(sdp, kind)
answerer.on_description = lambda sdp, kind: offerer.set_remote_description(sdp, kind)
offerer.on_candidate = lambda cand, mid: answerer.add_remote_candidate(cand, mid)
answerer.on_candidate = lambda cand, mid: offerer.add_remote_candidate(cand, mid)
digest, done = hashlib.sha256(), threading.Event()


def arrived(channel):
    def message(data):
        if data == 'end':
            done.set()
        elif isinstance(data, bytes):
            digest.update(data)
    channel.on_message = message


answerer.on_channel = arrived
channel = offerer.create_channel('check')
if not channel.opened.wait(15):
    sys.exit('libdatachannel: the link did not open')
blob = os.urandom(4 * 1024 * 1024)
for offset in range(0, len(blob), 60 * 1024):
    channel.send(blob[offset:offset + 60 * 1024])
channel.send_text('end')
if not done.wait(30) or digest.hexdigest() != hashlib.sha256(blob).hexdigest():
    sys.exit('libdatachannel: the data did not arrive intact')
offerer.close()
answerer.close()
print('libdatachannel: 4 MB across a link, intact')
