"""A theme file's page as the renderer's helper loads it (modules.themes,
subtitle_styles/host.js): its policy lets Subtitld's script run and stops
the page's own; host.js fills a slotted theme's slots, cuts its text into
lines, words and characters, marks empty slots, repeats list items, sets
the stage's variables and the outro's class, seeks the outro's animations
from the outro's start, and leaves a theme without slots its own stage.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself.
"""
import json, os, sys, tempfile, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '') + ' --disable-gpu'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtCore import QEventLoop, QTimer, QUrl, Qt
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import session, themes

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def spin(seconds):
    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()


class Page(QWebEnginePage):
    def __init__(self):
        super().__init__()
        self.messages = []

    def javaScriptConsoleMessage(self, level, message, line, source):
        self.messages.append(message)


HOST = (session.PATH_SUBTITLD / 'subtitle_styles' / 'host.js').read_text(encoding='utf-8')


def open_page(path):
    view = QWebEngineView()
    page = Page()
    view.setPage(page)
    view.setAttribute(Qt.WA_DontShowOnScreen)
    view.resize(960, 540 + 12)
    view.show()
    script = QWebEngineScript()
    script.setSourceCode(HOST)
    script.setInjectionPoint(QWebEngineScript.DocumentReady)
    script.setWorldId(QWebEngineScript.MainWorld)
    page.scripts().insert(script)
    view.load(QUrl.fromLocalFile(str(path)))
    deadline = time.monotonic() + 20
    while 'SUBTITLD_READY' not in page.messages and time.monotonic() < deadline:
        spin(0.05)
    return view, page


def evaluate(page, code):
    result = []
    # As JSON: Qt hands null back as '' and drops what it cannot convert.
    page.runJavaScript(f'JSON.stringify({code})', 0, lambda value: result.append(value))
    deadline = time.monotonic() + 10
    while not result and time.monotonic() < deadline:
        spin(0.02)
    return json.loads(result[0]) if result and result[0] else None


def render(page, **job):
    base = {'id': 1, 'html': 'x', 'align': 2, 'vars': {}, 'attrs': {}, 'aspect': 16 / 9, 'safe': [0.1, 0.1],
            'offset': 0, 'duration': 2000, 'pad': 0.03, 'place': None, 'direction': None, 'bidi': None,
            'look': None, 'writing': None, 'view': [960, 552], 'slots': None, 'slot_types': None, 'free': False,
            'slice': 'none', 'outro': 0}
    base.update(job)
    before = sum(1 for m in page.messages if m.startswith('SUBTITLD_RENDERED'))
    page.runJavaScript(f'subtitld.render({json.dumps(base)})')
    deadline = time.monotonic() + 15
    while sum(1 for m in page.messages if m.startswith('SUBTITLD_RENDERED')) == before and time.monotonic() < deadline:
        spin(0.03)
    answers = [m for m in page.messages if m.startswith('SUBTITLD_RENDERED')]
    return json.loads(answers[-1].split(' ', 1)[1]) if len(answers) > before else None


folder = Path(tempfile.mkdtemp())

print('the policy')
# Built by hand: a check-passing file could not have these, which is the point.
probe = folder / 'probe.png'
probe.write_bytes(b'\x89PNG\r\n\x1a\n')
raw = ('<!doctype html><html><head><meta charset="utf-8">'
       '<script type="application/json" id="subtitld-theme">{"format": 1, "id": "t.probe", "kind": "subtitle", "name": "P"}</script>'
       '</head><body><script>console.log("PAGE_SCRIPT_RAN")</script>'
       f'<img src="{probe.as_uri()}" onerror="console.log(\'ONERROR_RAN\')"></body></html>')
theme = themes.Theme(id='t.probe', kind='subtitle', name='P', html=raw)
served = themes.served(theme)
check('the copy has the policy after the doctype', served.read_text().startswith('<!doctype html>\n<meta http-equiv="Content-Security-Policy"'), True)
check('a second call reuses it', themes.served(theme), served)
view, page = open_page(served)
check('host.js runs under the policy', 'SUBTITLD_READY' in page.messages, True)
check('the page\'s own script does not', 'PAGE_SCRIPT_RAN' in page.messages, False)
check('nor an onerror', 'ONERROR_RAN' in page.messages, False)
check('a file image is not loaded', evaluate(page, 'document.querySelector("img") ? document.querySelector("img").naturalWidth : 0'), 0)
check('the policy is in force', any('Content Security Policy' in m for m in page.messages), True)
view.close()
changed = themes.Theme(id='t.probe', kind='subtitle', name='P', html=raw.replace('"P"', '"Q"'))
check('a new version replaces the old copy', (themes.served(changed).exists(), served.exists()), (True, False))

print('a slotted title theme')
manifest = {
    'format': 1, 'id': 't.card', 'kind': 'title', 'name': 'Card', 'slice': 'chars',
    'timing': {'intro': 400, 'outro': 500},
    'slots': [{'key': 'main'}, {'key': 'role'}, {'key': 'logo', 'type': 'image'},
              {'key': 'people', 'type': 'list'}],
}
html = ('<!doctype html><html><head><meta charset="utf-8">'
        f'<script type="application/json" id="subtitld-theme">{json.dumps(manifest)}</script>'
        '<style>html { background: red } .card { font: 40px sans-serif; color: white; animation: in 400ms linear both }'
        ' .theme-out .card { animation: out 500ms linear both }'
        ' @keyframes in { from { opacity: 0 } } @keyframes out { to { opacity: 0 } }</style>'
        '</head><body><div class="card"><h1 data-slot="main">Sample</h1><p data-slot="role">Role</p>'
        '<img data-slot="logo" src="data:image/png;base64,iVBORw0KGgo=">'
        '<ul data-slot="people"><li><b data-item>Someone</b></li></ul></div></body></html>')
theme = themes.parse(html)
check('it reads as slotted', theme.slotted, True)
view, page = open_page(themes.served(theme))
png = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='
answer = render(page, html='Hello <i>big</i> world<br>again', slots={'role': None, 'logo': png, 'people': ['Ana', 'Bo']},
                slot_types={'main': 'text', 'role': 'text', 'logo': 'image', 'people': 'list'}, slice='chars',
                outro=500, offset=100)
check('rendered, with a place', answer is not None and answer['rect'] is not None, True)
check('the sample is gone from the body', evaluate(page, 'document.body.textContent.includes("Sample")'), False)
check('lines', evaluate(page, '[...document.querySelectorAll("[data-slot=main] .line")].map(l => l.textContent)'),
      ['Hello big world', 'again'])
check('words', evaluate(page, '[...document.querySelectorAll("[data-slot=main] .word")].map(w => w.textContent)'),
      ['Hello', 'big', 'world', 'again'])
check('formatting kept around a word', evaluate(page, 'document.querySelector("[data-slot=main] i .word").textContent'), 'big')
check('chars, numbered through the slot',
      evaluate(page, '[...document.querySelectorAll("[data-slot=main] .char")].slice(5, 8).map(c => c.textContent + c.style.getPropertyValue("--i"))'),
      ['b5', 'i6', 'g7'])
check('counts on the slot', evaluate(page, '(s => [s.getPropertyValue("--lines"), s.getPropertyValue("--words"), s.getPropertyValue("--chars")])(document.querySelector("[data-slot=main]").style)'),
      ['2', '4', '18'])
check('an empty slot: emptied and marked', evaluate(page, '(e => [e.textContent, e.hasAttribute("data-empty")])(document.querySelector("[data-slot=role]"))'),
      ['', True])
check('the image slot', evaluate(page, 'document.querySelector("[data-slot=logo]").getAttribute("src") === ' + json.dumps(png)), True)
check('list items from the pattern', evaluate(page, '[...document.querySelectorAll("[data-slot=people] li b")].map(b => b.textContent)'),
      ['Ana', 'Bo'])
check('list items numbered', evaluate(page, '[...document.querySelectorAll("[data-slot=people] li")].map(li => li.style.getPropertyValue("--i"))'),
      ['0', '1'])
check('the stage\'s variables', evaluate(page, '(s => [s.getPropertyValue("--stage-height"), s.getPropertyValue("--safe-top"), s.getPropertyValue("--cue-time")])(document.documentElement.style)'),
      ['1080px', '108px', '100ms'])
check('the body\'s background stays clear', evaluate(page, 'getComputedStyle(document.body).backgroundColor'), 'rgba(0, 0, 0, 0)')
check('intro: seeked to the moment', evaluate(page, 'Math.round(parseFloat(getComputedStyle(document.querySelector(".card")).opacity) * 100)'), 25)
check('no outro yet', evaluate(page, 'document.documentElement.classList.contains("theme-out")'), False)
render(page, html='Bye', slots={}, slot_types={'main': 'text'}, slice='chars', outro=500, offset=1750)
check('outro: the class', evaluate(page, 'document.documentElement.classList.contains("theme-out")'), True)
check('outro: seeked from its start (250 of 500 ms)',
      evaluate(page, 'Math.round(parseFloat(getComputedStyle(document.querySelector(".card")).opacity) * 100)'), 50)
render(page, html='Back', slots={}, slot_types={'main': 'text'}, slice='words', outro=500, offset=0)
check('the class goes with the next cue', evaluate(page, 'document.documentElement.classList.contains("theme-out")'), False)
check('slice words: no chars', evaluate(page, 'document.querySelectorAll("[data-slot=main] .char").length'), 0)
render(page, html='Free', slots={}, slot_types={'main': 'text'}, free=True)
check('a theme that places itself: over the whole stage',
      evaluate(page, '!!document.querySelector("#subtitld-stage > .cue.subtitld-free") && !document.querySelector("#safe .cue")'), True)
view.close()

print('a theme without slots')
view, page = open_page(themes.served(themes.load(session.PATH_SUBTITLD / 'themes' / 'classic.html')))
render(page, html='Hello <i>world</i>', slots={}, slot_types={'main': 'text'})
check('the cue on Subtitld\'s stage', evaluate(page, 'document.querySelector("#safe > .cue").innerHTML'), 'Hello <i>world</i>')
check('the sample is gone', evaluate(page, 'document.querySelector(".sample")'), None)
check('not cut', evaluate(page, 'document.querySelectorAll(".word").length'), 0)
view.close()

print()
print('ALL PASS' if not fails else f'{len(fails)} FAILED: {fails}')
sys.exit(1 if fails else 0)
