"""Audio panel: the effects mixer (tracks, chains, devices, meters) and the
recording tab, plus the widgets they are drawn with.

The audio engine is a stand-in: real tracks and real effect chains, as the
engine builds them, but no sound device. Input devices and transcription
engines are stand-ins too.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile, types
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import numpy as np
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QStackedWidget
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
app = QApplication([])
from subtitld.modules import session, audio_effects, audioengine
from subtitld.modules import recorder as recorder_mod
from subtitld.interface import utils
from subtitld.interface import audio_widgets as aw
from subtitld.interface import left_panel_audio as lpa

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


print('the EQ curve is the sound: the same filters the processor runs')
params = audio_effects.default_params('eq')
freqs = np.array([20.0, 1000.0, 20000.0])
check('flat when every band is at 0 dB', np.round(audio_effects.eq_response(params, freqs), 3).tolist(), [0.0, 0.0, 0.0])
params['bands'][1]['gain'] = 6.0
response = audio_effects.eq_response(params, freqs)
check('a +6 dB mid band is +6 at its frequency, flat far away',
      (round(float(response[1]), 1), abs(response[0]) < 0.2, abs(response[2]) < 0.2), (6.0, True, True))
check('compressor curve: unchanged below the threshold, divided by the ratio above',
      audio_effects.compressor_output_db({'threshold_db': -20, 'ratio': 4, 'makeup_db': 2}, [-40, -20, 0]).tolist(),
      [-38.0, -18.0, -13.0])
check('gate curve: silent below the threshold', audio_effects.gate_output_db({'threshold_db': -45}, [-60, -10]).tolist(),
      [-120.0, -10.0])

print('the compressor and gate report what they did, for the meters')
compressor = audio_effects.CompressorProcessor({'threshold_db': -20, 'ratio': 4}, 48000)
for _ in range(5):
    compressor.process(np.full((4800, 2), 0.5, np.float32))
check('input -6 dB, 14 over a 4:1 threshold: 10.5 dB less',
      (round(compressor.last_input_db, 1), round(compressor.last_reduction_db, 1)), (-6.0, -10.5))
gate = audio_effects.GateProcessor({'threshold_db': -30}, 48000)
for _ in range(5):
    gate.process(np.full((4800, 2), 0.001, np.float32))
check('a quiet signal under a gate: closed', gate.last_reduction_db < -40, True)

print('a track keeps the peak of what it played, for its meter')


class Clip:
    def read(self, playhead, frames, samplerate, pool):
        return np.full((frames, 2), 0.25, np.float32)


class Pool:
    def get(self, shape, dtype):
        return np.zeros(shape, dtype)

    def release(self, block):
        pass


track = audioengine.Track()
track.add_clip(Clip())
track.gain = 2.0
track.read(0.0, 256, 48000, Pool())
check('after effects and gain', track.last_peak, 0.5)
track.enabled = False
track.read(0.0, 256, 48000, Pool())
check('a silenced track reads 0', track.last_peak, 0.0)

print('knobs')
knob = aw.Knob('Freq', 20, 20000, 1000, log=True)
check('a log knob maps its ends and middle', (round(knob.to_unit(20), 3), round(knob.to_unit(20000), 3),
                                               round(knob.from_unit(0.5))), (0.0, 1.0, 632))
seen = []
knob.valueChanged.connect(seen.append)
knob.setValue(50000, emit=True)
check('held to its range', knob.value(), 20000.0)
knob.show()
QTest.mouseDClick(knob, Qt.LeftButton)
check('double-click: back to the default', knob.value(), 1000.0)
QTest.keyClick(knob, Qt.Key_Up)
check('an arrow key nudges it, and says so', (knob.value() > 1000, len(seen)), (True, 3))
start = knob.value()
QTest.mousePress(knob, Qt.LeftButton, Qt.NoModifier, QPoint(29, 40))
QTest.mouseMove(knob, QPoint(29, 0))
QTest.mouseRelease(knob, Qt.LeftButton, Qt.NoModifier, QPoint(29, 0))
check('dragging up turns it up', knob.value() > start, True)
gain = aw.Knob('Gain', -24, 24, 0)
check('a gain knob starts from the middle', gain.to_unit(0), 0.5)

print('level meter')
meter = aw.LevelMeter()
meter.set_level(1.0)
check('full scale is 0 dB', meter.level_db(), 0.0)
meter.set_level(0.0)
check('it falls rather than drop', meter.level_db() > meter.FLOOR, True)


# --------------------------------------------------------------------------- #
# The panel                                                                    #
# --------------------------------------------------------------------------- #

class FakeEngine:
    """The engine's effect plumbing, without a sound device."""

    def __init__(self):
        self.background_sound = audioengine.Track()
        self.vocals_sound = audioengine.Track()
        self.speaker_tracks = {'A': audioengine.Track()}
        self.samplerate = 48000
        self.audio_effects = []
        self.pushes = 0

    set_audio_effects = audioengine.SoundDeviceAudioEngine.set_audio_effects
    apply_effects = audioengine.SoundDeviceAudioEngine.apply_effects
    _apply_effects_to_track = audioengine.SoundDeviceAudioEngine._apply_effects_to_track


engine = FakeEngine()
playing = {'on': False}
recorder_mod.list_input_devices = lambda: [(3, 'Studio mic'), (5, 'Laptop mic')]
recorder_mod.default_input_device = lambda: 5
lpa._asr_providers = lambda: [types.SimpleNamespace(id='realtime-stt', display_name='RealtimeSTT')]


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.resize(460, 1400)
        self.setLayout(QVBoxLayout())
        self.left_panel_navigation = QWidget()
        self.left_panel_navigation.setLayout(QVBoxLayout())
        self.left_panel_navigation.layout().addStretch()
        self.left_panel_stackedwidgets = QStackedWidget()
        self.layout().addWidget(self.left_panel_stackedwidgets)
        self.preview_panel_player = types.SimpleNamespace(_audio_device=engine,
                                                          is_paused=lambda: not playing['on'])
        self.record_controller = None


session.CONFIG = {'record': {}}
session.VIDEO = {'filepath': '/videos/talk.mp4'}
session.SUBTITLE = {'filepath': '', 'segments': [], 'selected': None}
session.SPEAKERS = {'A': {'color': '#e57373'}, 'B': {'color': '#64b5f6'}}
host = Host()
lpa.load(host)
lpa.translate(host)
host.show()
lpa.update(host)
app.processEvents()

print('recording tab: the labelled comboboxes')
check('engine and device are labelled blocks',
      (isinstance(host.audio_record_engine_block, utils.LabeledComboBox),
       isinstance(host.audio_record_device_block, utils.LabeledComboBox)), (True, True))
check('labels', (host.audio_record_engine_block.label.text(), host.audio_record_device_block.label.text()),
      ('TRANSCRIPTION ENGINE', 'INPUT DEVICE'))
check('devices listed, the default chosen', (host.audio_record_device_combobox.count(),
                                             host.audio_record_device_combobox.currentText()), (2, 'Laptop mic'))
host.audio_record_device_combobox.setCurrentIndex(0)
host.audio_record_device_combobox.activated.emit(0)
check('choosing one is remembered', session.CONFIG['record']['device'], 3)

print('recording tab: the input monitor')
host.left_panel_audio_tabwidget.setCurrentIndex(1)
app.processEvents()
host.record_controller = types.SimpleNamespace(is_recording=True, level=0.5, elapsed=75, status='recording',
                                               interim_text='hello wor')
lpa._tick(host)
check('the meter shows the input', round(host.audio_record_level.level_db(), 1), -6.0)
check('the clock runs', host.audio_record_elapsed.text(), '01:15')
check('the light is live', host.audio_record_status_light.property('state'), 'live')
check('interim text shown', (host.audio_record_interim.isVisible(), host.audio_record_interim.text()), (True, '… hello wor'))
host.record_controller = types.SimpleNamespace(is_recording=False, level=0, elapsed=0, status='no-input', interim_text='')
lpa._tick(host)
check('a problem after stopping stays, in amber', (host.audio_record_status_light.property('state'),
                                                     host.audio_record_status.text()),
      ('problem', lpa._('audio_panel.no_input_device')))
host.left_panel_audio_tabwidget.setCurrentIndex(0)
app.processEvents()

print('effects: the tracks')
rows = host._fx_rows
check('background, voice and each speaker', list(rows), [('background', None), ('voice', None), ('speaker', 'A'), ('speaker', 'B')])
check('the background music is selected first', host._fx_track, ('background', None))
check('an empty chain says so, and offers the add slot',
      (host.audio_fx_chain_hint.text(), host.audio_fx_chain_layout.count()), (lpa._('audio_panel.chain_empty'), 1))

print('effects: adding devices to the selected track')
lpa._fx_add(host, 'eq')
lpa._fx_add(host, 'compressor')
app.processEvents()
model = host._audio_effects_model
check('both on the background', [(s['type'], s['target']['kind']) for s in model], [('eq', 'background'), ('compressor', 'background')])
check('two cards, joined by connectors, then the add slot', [type(host.audio_fx_chain_layout.itemAt(i).widget()).__name__
                                                             for i in range(host.audio_fx_chain_layout.count())],
      ['_DeviceCard', '_Connector', '_DeviceCard', '_Connector', 'QPushButton'])
check('saved for the project', len(audio_effects.load_effects()), 2)
check('running on the engine\'s background track', bool(engine.background_sound.effect_chain), True)
check('the track row counts them', host._fx_rows[('background', None)].count.text(), '2')

print('effects: another track has its own chain')
host._fx_rows[('speaker', 'A')].clicked.emit()
app.processEvents()
check('selected', host._fx_track, ('speaker', 'A'))
check('its chain is empty', len(host._fx_cards), 0)
lpa._fx_add(host, 'gate')
check('a gate on speaker A', (model[-1]['type'], model[-1]['target']), ('gate', {'kind': 'speaker', 'speaker': 'A'}))
check('on that speaker\'s track only', (engine.speaker_tracks['A'].effect_chain is not None,
                                        len(engine.background_sound.effect_chain._entries)), (True, 1))
host._fx_rows[('background', None)].clicked.emit()
app.processEvents()
eq_card, comp_card = host._fx_cards

print('effects: knobs set the parameters, and the engine follows')
comp_card.knobs['ratio'].setValue(8.0, emit=True)
comp_spec = comp_card.spec
check('ratio stored', comp_spec['params']['ratio'], 8.0)
processor = engine.background_sound.effect_chain.processor_for(comp_spec['id'])
check('the running compressor has it', processor.ratio, 8.0)
comp_card.graph.thresholdChanged.emit(-30.0)
comp_spec['params']['threshold_db'] = -30.0
check('dragging the threshold moves its knob', comp_card.knobs['threshold_db'].value(), -30.0)

print('effects: the EQ graph — drag a band')
graph = eq_card.graph
graph.select(2)
check('selecting a band shows its knobs', (eq_card.band_buttons[2].isChecked(), eq_card.knobs['freq'].value()), (True, 8000.0))
point = graph._handle(2, graph.plot_rect())
QTest.mousePress(graph, Qt.LeftButton, Qt.NoModifier, point.toPoint())
target = QPoint(int(graph._fx(4000, graph.plot_rect())), int(graph._fy(6, graph.plot_rect())))
QTest.mouseMove(graph, target)
QTest.mouseRelease(graph, Qt.LeftButton, Qt.NoModifier, target)
band = eq_card.spec['params']['bands'][2]
check('the band moved to about 4 kHz, +6 dB', (3500 < band['freq'] < 4600, band['gain']), (True, 6.0))
check('its knobs follow', (round(eq_card.knobs['gain'].value(), 1), abs(eq_card.knobs['freq'].value() - band['freq']) < 1), (6.0, True))
eq_card.knobs['q'].setValue(2.0, emit=True)
check('the Q knob sets that band\'s width', band['q'], 2.0)
check('and the engine has the new curve', engine.background_sound.effect_chain.processor_for(eq_card.spec['id']) is not None, True)

print('effects: order, bypass, range, remove')
lpa._fx_move(host, comp_spec, -1)
check('moved up the chain', [s['type'] for s in lpa._fx_chain(host)], ['compressor', 'eq'])
check('the other track is untouched', [s['type'] for s in lpa._fx_chain(host, ('speaker', 'A'))], ['gate'])
check('the engine runs them in the new order', [spec['type'] for spec, _p in engine.background_sound.effect_chain._entries],
      ['compressor', 'eq'])
app.processEvents()      # new cards are shown on the next pass of the event loop
comp_card = [card for card in host._fx_cards if card.kind == 'compressor'][0]
check('first card: up disabled', [b.isEnabled() for b in comp_card.findChildren(lpa.QPushButton, 'audio_fx_icon_button')][:2], [False, True])
comp_card.power.click()
check('bypassed: off in the model and the engine', (comp_spec['enabled'], engine.background_sound.effect_chain.processor_for(comp_spec['id'])),
      (False, None))
check('dimmed, still editable', (comp_card._opacity.isEnabled(), comp_card.knobs['ratio'].isEnabled()), (True, True))
comp_card.power.click()
check('back on', engine.background_sound.effect_chain.processor_for(comp_spec['id']) is not None, True)

comp_card.range_chip.click()
check('the chip opens the time range', comp_card.range_editor.isVisible(), True)
check('whole timeline at first', (comp_card.range_all.isChecked(), comp_card.range_chip.text()), (True, 'ALL'))
comp_card.range_some.click()
check('part of it', comp_spec['range'], [0.0, 10.0])
comp_card.range_to.setValue(30.5)
check('edited', (comp_spec['range'], comp_card.range_chip.text()), ([0.0, 30.5], '0:00.0 – 0:30.5'))
session.SUBTITLE['selected'] = {'start': 12.0, 'end': 14.5, 'text': 'x'}
comp_card._show_range()
comp_card.range_selected.click()
check('from the selected subtitle', comp_spec['range'], [12.0, 14.5])
comp_card.range_all.click()
check('whole again', comp_spec['range'], None)

print('effects: folding is remembered while the panel lives')
comp_card._toggle_fold()
check('folded', comp_card.body.isHidden(), True)
lpa._fx_rebuild(host)
comp_card = [card for card in host._fx_cards if card.kind == 'compressor'][0]
check('still folded after a rebuild', comp_card.body.isHidden(), True)
comp_card._toggle_fold()

print('effects: the meters follow playback')
engine.background_sound.last_peak = 0.5
playing['on'] = True
lpa._fx_tick_meters(host)
check('the track meter shows it', round(host._fx_rows[('background', None)].meter.level_db(), 1), -6.0)
processor = engine.background_sound.effect_chain.processor_for(comp_spec['id'])
processor.last_input_db, processor.last_reduction_db = -9.0, -4.0
lpa._fx_tick_meters(host)
check('the compressor shows its input and reduction', (comp_card.graph.input_db, comp_card.graph.reduction_db), (-9.0, -4.0))
playing['on'] = False
lpa._fx_tick_meters(host)
check('paused: no live dot', comp_card.graph.input_db, None)

print('effects: removing')
lpa._fx_remove(host, comp_spec)
check('gone from the chain and the engine', ([s['type'] for s in lpa._fx_chain(host)],
                                             [spec['type'] for spec, _p in engine.background_sound.effect_chain._entries]),
      (['eq'], ['eq']))

print('effects: a speaker that was removed keeps its effects visible')
session.SPEAKERS.pop('A')
lpa._fx_rebuild(host)
check('still listed', ('speaker', 'A') in host._fx_rows, True)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
