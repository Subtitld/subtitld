"""subtitld.cc integration: USF origin and exact times, what gets published, opening a subtitle in the
editor, and the panel (lookup results, publish card) against a fake subtitld.cc, so no network is used.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QStackedWidget, QLabel
app = QApplication([])
from subtitld.modules import session, usf
from subtitld.modules import subtitldcc_service as service
from subtitld.modules.subtitldcc import fingerprint
from subtitld.interface import left_panel_subtitldcc as panel_module

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


def wait(condition, seconds=5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


print('USF times are exact milliseconds; the old 23:59:59.999 start reads as 0')
check('written', [usf.format_time(s) for s in (0.0, 1.5, 2.002, 3599.999, 7322.5)],
      ['00:00:00.000', '00:00:01.500', '00:00:02.002', '00:59:59.999', '02:02:02.500'])
check('read back', [usf.parse_time(usf.format_time(s)) for s in (0.0, 2.002, 61.123)], [0.0, 2.002, 61.123])
check('legacy zero', usf.parse_time('23:59:59.999'), 0.0)
check('short forms', (usf.parse_time('1:02.5'), usf.parse_time('00:00:01.0004')), (62.5, 1.0))

print('the subtitld.cc origin survives a save and a reload')
content = usf.USFWriter().write([{'start': 0.0, 'end': 1.25, 'text': 'Olá', 'speaker': 'A'}], language='pt-BR',
                               origin={'share_id': 'Xk3pQ9aZ2bT', 'version': 3})
check('in our namespace', ('xmlns:subtitldcc="https://subtitld.cc/ns/usf/1"' in content,
                           '<subtitldcc:origin share-id="Xk3pQ9aZ2bT" version="3"/>' in content), (True, True))
reader = usf.USFReader()
segments = reader.read(content)
check('read back', (reader.origin, segments[0]['start'], segments[0]['end']),
      ({'share_id': 'Xk3pQ9aZ2bT', 'version': 3}, 0.0, 1.25))
reader.read(usf.USFWriter().write([{'start': 0.0, 'end': 1.0, 'text': 'x'}]))
check('none when absent', reader.origin, None)
spoofed = content.replace('https://subtitld.cc/ns/usf/1', 'https://example.com/other')
reader.read(spoofed)
check('only our namespace counts', reader.origin, None)

print('language codes as subtitld.cc expects them')
known = ['en', 'pt', 'pt-BR', 'zh-Hans', 'es-419']
check('regional', service.bcp47('pt-br', known), 'pt-BR')
check('underscore', service.bcp47('en_US', known), 'en')
check('script', service.bcp47('zh-hans', known), 'zh-Hans')
check('unknown', service.bcp47('xx', known), '')

print('what gets published: one language, no dubbing, images or file paths')
session.CONFIG = {}
session.SUBTITLE = {'language': 'en', 'filepath': '', 'segments': [
    {'start': 1.0, 'end': 2.0, 'text': 'Hello', 'speaker': 'Ann', 'translations': {'pt-BR': 'Olá'},
     'dubbing': [{'path': '/home/me/secret/dub1.wav', 'start': 1.0}]},
    {'start': 3.0, 'end': 4.0, 'text': 'Bye', 'speaker': 'Ann', 'translations': {'pt-BR': ''}},
]}
session.SPEAKERS = {'Ann': {'color': '#ff0000', 'image_bytes': b'PNG', 'dubbing': {'engine': 'edge-tts'}}}
original = service.usf_for_publishing().decode()
check('original text, no dubbing or images', ('Hello' in original, 'Olá' in original, 'secret' in original,
                                              'avatar' in original, 'edge-tts' in original),
      (True, False, False, False, False))
check('speaker colors kept', '#ff0000' in original, True)
translated = service.usf_for_publishing('pt-BR').decode()
check('the translation alone, empty lines left out', ('Olá' in translated, 'Hello' in translated,
                                                      translated.count('<subtitle '), 'code="pt-BR"' in translated),
      (True, False, 1, True))
check('texts on offer', service.text_languages(), [('en', False), ('pt-BR', True)])

print('the bundled client fingerprints a video without reading all of it')
video = Path(_xdg) / 'video.mkv'
video.write_bytes(os.urandom(300 * 1024))
print_ = fingerprint(video, 12000)
check('both hashes, size, duration', (len(print_['subtitld_hash']), len(print_['opensubtitles_hash']),
                                      print_['size'], print_['duration_ms']), (64, 16, 300 * 1024, 12000))
check('never the name', 'video.mkv' in str(print_), False)


# --- The panel, against a fake subtitld.cc -------------------------------------------------------------------

RESULT = {'share_id': 'Xk3pQ9aZ2bT', 'url': 'https://subtitld.cc/Xk3pQ9aZ2bT', 'title': 'Sintel (2010)',
          'language': 'pt-BR', 'owner': 'marina', 'visibility': 'public', 'version': 2, 'offset_ms': 0,
          'downloads': 1234, 'rating': {'average': 4.5, 'count': 12}, 'top_tags': [], 'flags': ['hearing_impaired']}
DOWNLOADED = usf.USFWriter().write([{'start': 0.0, 'end': 2.0, 'text': 'Primeira', 'speaker': 'A'},
                                    {'start': 3.0, 'end': 4.0, 'text': 'Segunda', 'speaker': 'B'}],
                                   language='pt-BR', origin={'share_id': 'Xk3pQ9aZ2bT', 'version': 2}).encode()
calls = []


class FakeClient:
    def meta(self):
        return {'languages': ['en', 'pt-BR'], 'language_names': {'en': 'English', 'pt-BR': 'Português (Brasil)'},
                'licenses': [{'key': 'CC-BY-4.0', 'name': 'CC BY 4.0'}]}

    def me(self):
        return {'handle': 'marina', 'name': 'Marina', 'publish_as': ['marina'], 'subtitle_languages': ['pt-BR']}

    def lookup(self, videos, languages=None):
        calls.append(('lookup', videos[0].get('subtitld_hash') is not None, languages))
        return [{'index': 0, 'exact': [RESULT], 'probable': []}]

    def download(self, share_id, fmt='usf', version=None):
        calls.append(('download', share_id, fmt))
        return DOWNLOADED

    def asset(self, share_id):
        return {**RESULT, 'permissions': {'can_edit': True, 'can_share': True, 'can_rate': False,
                                          'can_delete': True},
                'my_rating': None}

    def delete(self, share_id):
        calls.append(('delete', share_id))

    def check(self, name, data, target=None):
        calls.append(('check', name, target, data))
        time.sleep(0.2)  # a slow upload: Subtitld must stay usable meanwhile
        return {'id': 'u1', 'problems': [], 'suggestions': [], 'cue_count': 2, 'fixes': [], 'language': 'pt-BR',
                'title_hint': 'Sintel', 'year_hint': 2010}

    def discard_upload(self, upload_id):
        calls.append(('discard', upload_id))

    def new_version(self, share_id, upload_id, *, parent, changelog, kind='fix', video=None, idempotency_key=None):
        calls.append(('new_version', share_id, parent, changelog))
        return {'asset': {**RESULT, 'version': 3}, 'version': 3}


service.client = lambda token=None: FakeClient()


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.resize(420, 900)
        self.setLayout(QVBoxLayout())
        self.left_panel_navigation = QWidget()
        self.left_panel_navigation.setLayout(QVBoxLayout())
        self.left_panel_navigation.layout().addStretch()
        self.layout().addWidget(self.left_panel_navigation)
        self.left_panel_stackedwidgets = QStackedWidget()
        self.layout().addWidget(self.left_panel_stackedwidgets)
        self.titleBar_left_information_label = QLabel()


session.CONFIG = {}
session.SUBTITLE = {'segments': [], 'filepath': ''}
session.SPEAKERS = {}
session.VIDEO = {}
host = Host()
panel_module.load(host)
panel_module.translate(host)
host.show()
panel = host.subtitldcc_panel
check('nothing fetched before the panel is open', service.cached_meta(), None)
host.left_panel_stackedwidgets.setCurrentIndex(0)
panel.refresh()
check('fetched once open', wait(lambda: service.cached_meta() is not None), True)

print('not connected: lookups still work, publishing waits for a connection')
check('connect offered', (panel.connect_button.isVisibleTo(host), panel.disconnect_button.isVisibleTo(host)),
      (True, False))
check('publish disabled', panel.publish_new_button.isEnabled(), False)

print('a video opens: it is looked up by fingerprint')
session.VIDEO = {'filepath': str(video), 'duration': 12.0}
panel_module.video_opened(host)
check('looked up', wait(lambda: panel._lookup_call is None and panel._matches is not None), True)
check('by hash', calls[0][:2], ('lookup', True))
rows = [panel.video_results.itemAt(i).widget() for i in range(panel.video_results.count())]
check('one row', len(rows), 1)
check('described', panel_module.describe_subtitle(RESULT),
      'Português (Brasil) · v2 · ★ 4.5 (12) · 1,234 downloads · @marina')
nav = [b for b in host.findChildren(panel_module.left_panel.navigation_button) if b.tab_name == 'subtitldcc']
check('the navigation button says so', nav[0].property('has_matches'), 'true')

print('turned off: nothing is sent when a video opens')
panel.auto_lookup.setChecked(False)
calls.clear()
panel_module.video_opened(host)
app.processEvents()
check('no lookup', calls, [])
check('setting kept', service.settings()['auto_lookup'], False)
panel.auto_lookup.setChecked(True)
wait(lambda: panel._lookup_call is None and panel._matches is not None)

print('opening over subtitles in the editor asks first, in the row (never a dialog)')
session.SUBTITLE['segments'] = [{'start': 0.0, 'end': 1.0, 'text': 'mine', 'speaker': 'A'}]
calls.clear()
rows = [panel.video_results.itemAt(i).widget() for i in range(panel.video_results.count())]
rows[0].open_button.click()
app.processEvents()
check('asks in the row', (rows[0].confirm.isVisibleTo(host), 'Sintel (2010)' in rows[0].confirm.text.text()),
      (True, True))
check('nothing downloaded yet', calls, [])
rows[0].confirm.no.click()
check('cancel keeps the editor', (rows[0].confirm.isVisibleTo(host), session.SUBTITLE['segments'][0]['text']),
      (False, 'mine'))

print('opening a subtitle replaces the editor\'s and remembers where it came from')
rows[0].open_button.click()
rows[0].confirm.yes.click()
check('opened', wait(lambda: len(session.SUBTITLE.get('segments', [])) == 2), True)
check('texts', [s['text'] for s in session.SUBTITLE['segments']], ['Primeira', 'Segunda'])
check('origin', service.origin(), {'share_id': 'Xk3pQ9aZ2bT', 'version': 2})
check('not a file yet, nothing unsaved', (session.SUBTITLE['filepath'], session.UNSAVED), ('', False))
check('speakers', sorted(session.SPEAKERS), ['A', 'B'])
wait(lambda: panel._origin_info and 'permissions' in panel._origin_info)
check('origin card', (panel.origin_card.isVisibleTo(host), panel.origin_title.text()), (True, 'Sintel (2010)'))

print('connecting: the browser answers, and the card says who is connected')
real_sign_in = service.sign_in


def fake_sign_in(device_code_shown=None, cancelled=lambda: False):
    time.sleep(0.2)
    service.store().save('sct_test')
    return FakeClient().me()


service.sign_in = fake_sign_in
panel.connect_button.click()
check('waiting while the browser is open', (panel.cancel_button.isVisibleTo(host),
                                             panel.connect_button.isVisibleTo(host)), (True, False))
check('done', wait(lambda: panel._sign_in_call is None), True)
app.processEvents()
check('connected, not waiting any more', (panel.disconnect_button.isVisibleTo(host),
                                          panel.cancel_button.isVisibleTo(host)), (True, False))
check('says who', '@marina' in panel.account_text.text(), True)
service.sign_in = real_sign_in

print('connected and allowed to edit: publish the next version')
panel.refresh()
check('connected', panel.disconnect_button.isVisibleTo(host), True)
check('version offered', (panel.publish_version_button.isVisibleTo(host), panel.publish_version_button.isEnabled()),
      (True, True))
check('text says which version', 'version 3 of “Sintel (2010)”' in panel.publish_text.text(), True)

print('publishing happens in the panel, and the editor stays usable while it uploads')
calls.clear()
panel.publish_version_button.click()
flow = panel._flow
check('the steps are in the panel', (flow is not None, flow.parent() is not None,
                                     panel.publish_buttons.isVisibleTo(host)), (True, True, False))
check('checking in the background', (flow.check_page.isVisibleTo(host), 'keep working' in flow.check_status.text()),
      (True, True))
session.SUBTITLE['segments'][0]['text'] = 'Primeira, editada'  # the person keeps working meanwhile
check('checked', wait(lambda: flow.upload is not None), True)
check('sent the subtitles as they were', b'Primeira</text>' in calls[0][3] and calls[0][2] == 'Xk3pQ9aZ2bT', True)
flow.next_button.click()
check('describe: what changed', (flow.describe_page.isVisibleTo(host), flow.changelog.isVisibleTo(host)),
      (True, True))
flow.changelog.setText('Fixed a typo.')
flow.next_button.click()
check('asks, in place, about the edit made after the check', (flow.question.isVisibleTo(host),
                                                               'changed after' in flow.question_text.text()),
      (True, True))
flow.question_no.click()  # "Publish as checked"
check('published', wait(lambda: flow.published is not None), True)
check('as the next version of what was opened', [c[:3] for c in calls if c[0] == 'new_version'],
      [('new_version', 'Xk3pQ9aZ2bT', 2)])
check('the link', (flow.done_page.isVisibleTo(host), flow.done_link.text()), (True, RESULT['url']))
check('the editor holds the new version right away, before Done', service.origin(),
      {'share_id': 'Xk3pQ9aZ2bT', 'version': 3})
check('says how to send changes later', 'Publish new version' in flow.done_page.findChildren(
    type(flow.done_text))[-1].text(), True)
flow.next_button.click()  # Done
app.processEvents()
check('back to the card', (panel._flow, panel.publish_buttons.isVisibleTo(host)), (None, True))
check('the editor now holds version 3', service.origin(), {'share_id': 'Xk3pQ9aZ2bT', 'version': 3})

print('deleting it from subtitld.cc asks first, and keeps the subtitles in the editor')
wait(lambda: panel._origin_info and 'permissions' in panel._origin_info)
panel._render_origin()
check('offered to its owner', panel.delete_button.isVisibleTo(host), True)
calls.clear()
panel.delete_button.click()
check('asks in place', (panel.delete_confirm.isVisibleTo(host), 'Sintel (2010)' in panel.delete_confirm.text.text()),
      (True, True))
check('nothing deleted yet', calls, [])
panel.delete_confirm.yes.click()
check('deleted', wait(lambda: ('delete', 'Xk3pQ9aZ2bT') in calls), True)
check('the editor forgets the link, keeps the text', wait(lambda: service.origin() is None), True)
check('still there', [s['text'] for s in session.SUBTITLE['segments']][:1], ['Primeira, editada'])
check('says so', 'deleted from subtitld.cc' in panel.message.text(), True)
check('the card goes away', panel.origin_card.isVisibleTo(host), False)

print('disconnecting asks in the card first')
panel.disconnect_button.click()
check('asks in place', panel.disconnect_confirm.isVisibleTo(host), True)
panel.disconnect_confirm.no.click()
check('still connected', service.is_connected(), True)

print('the token stops working: back to Connect')
panel._token_expired()
check('disconnected', (service.is_connected(), panel.connect_button.isVisibleTo(host)), (False, True))

print('subtitld.cc can ask for a newer Subtitld (development builds never count as old)')
import subtitld.modules.subtitldcc_service as service_module
service_module._meta = {**service.cached_meta(), 'min_subtitld_version': '26.10'}
for version, want in (('26.9', True), ('26.10', False), ('27.1.2', False), ('0.0.0.dev0', False)):
    service_module.__version__ = version
    check(f'{version} is outdated', service.outdated(), want)

panel.shutdown()
print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
