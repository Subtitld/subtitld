"""MXF checked by the reference tools: Subtitld's SMPTE DCP, IMF and EBU
STL track files given to asdcplib (asdcp-info, asdcp-unwrap, as-02-unwrap),
bmx (mxf2raw) and MXFDump, built by tests/corpus/build_tools.sh; and the
DCP track file Subtitle Edit keeps, made by asdcplib, read by Subtitld.

The tools are not packaged anywhere this runs, so the suite skips when they
have not been built; the asdcplib-made file comes from the corpus
(tests/corpus/fetch.py). No tool reads EBU STL in MXF (ST 2075): that one
gets MXFDump's structural check only.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, subprocess, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, mxf, session, ttml
from corpus_common import need

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


TOOLS = Path(os.environ.get('SUBTITLD_TOOLS') or Path.home() / '.cache' / 'subtitld' / 'tools')
if not all((TOOLS / 'bin' / tool).is_file() for tool in ('asdcp-info', 'asdcp-unwrap', 'as-02-unwrap', 'mxf2raw', 'MXFDump')):
    print(f'SKIP: MXF tools not built (sh tests/corpus/build_tools.sh); none in {TOOLS / "bin"}')
    sys.exit(0)
asdcplib_made, = need('subtitleedit/sample_mxf_429-5_asdcplib.mxf')
environment = dict(os.environ, LD_LIBRARY_PATH=os.pathsep.join(str(TOOLS / lib) for lib in ('lib', 'lib64')))


def run(tool, *arguments):
    done = subprocess.run([str(TOOLS / 'bin' / tool), *map(str, arguments)], capture_output=True, text=True,
                          env=environment, timeout=60)
    return done.returncode, done.stdout + done.stderr


folder = Path(tempfile.mkdtemp())
session.SUBTITLE['segments'] = [{'start': 1.0, 'end': 3.5, 'text': 'Hello there'},
                                {'start': 4.0, 'end': 6.0, 'text': 'Second <i>line</i>\nand more'}]
session.SUBTITLE['language'] = 'en'
session.VIDEO = {'framerate': 24.0}
session.FORMAT = {'options': {}}
for key in ('DCPMXF', 'IMSCMXF', 'STLMXF'):
    file_io.save_file(str(folder / f'{key}.mxf'), key)

print('MXFDump: every KLV, set and partition in its place')
for key in ('DCPMXF', 'IMSCMXF', 'STLMXF'):
    code, said = run('MXFDump', '-v', folder / f'{key}.mxf')
    check(f'{key}: no error or warning', (code, [line for line in said.splitlines()
                                                 if 'error' in line.lower() or 'warning' in line.lower()]), (0, []))

print('asdcplib: the DCP track file')
code, said = run('asdcp-info', '-i', folder / 'DCPMXF.mxf')
check('asdcp-info knows it: SMPTE timed text, 6 s at 24', (code, 'SMPTE 429 file essence type is Timed Text, (144 edit units)'
                                                          in said, 'Label Set Type: SMPTE' in said), (0, True, True))
code, said = run('asdcp-unwrap', folder / 'DCPMXF.mxf', folder / 'dcp-out')
check('asdcp-unwrap gives back the reel Subtitld wrapped', (code, (folder / 'dcp-out').read_bytes()
                                                            == mxf.read_timed_text(str(folder / 'DCPMXF.mxf'))['document']),
      (0, True))

print('asdcplib and bmx: the IMF track file')
code, said = run('as-02-unwrap', '-v', folder / 'IMSCMXF.mxf', folder / 'imf-out')
check('as-02-unwrap: the IMSC 1.1 text profile named, the document given back',
      (code, 'NamespaceName: http://www.w3.org/ns/ttml/profile/imsc1.1/text' in said,
       ttml.check((folder / 'imf-out').read_text(), 'imsc1.1')), (0, True, []))
code, said = run('mxf2raw', '--info', '--check-complete', folder / 'IMSCMXF.mxf')
check('mxf2raw opens it: OP1a, 144 edit units at 24', (code, "op_label        : OP1A" in said,
                                                      "duration        : 00:00:06:00 (count='144')" in said),
      (0, True, True))

print("asdcplib's own DCP track file, read by Subtitld")
code, said = run('asdcp-info', '-i', asdcplib_made)
carried = mxf.read_timed_text(str(asdcplib_made))
# asdcp-info's own reading of the file is the expectation: its edit units
# and rate, and the XML asdcp-unwrap takes out.
code, unwrapped = run('asdcp-unwrap', '-v', asdcplib_made, folder / 'theirs')
check('the XML asdcp-unwrap takes out, its rate and length as asdcp-unwrap and asdcp-info report them',
      (carried['document'] == (folder / 'theirs').read_bytes(),
       f'EditRate: {carried["rate"][0]}/{carried["rate"][1]}' in unwrapped,
       f'({carried["duration"]} edit units)' in said), (True, True, True))

print()
print('ALL PASS' if not fails else f'{len(fails)} FAILED: {fails}')
sys.exit(1 if fails else 0)
