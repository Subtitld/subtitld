#!/usr/bin/env python3
"""Generate haikuports recipes for Subtitld's Python dependencies.

Versions are pinned to what was verified working inside the Haiku VM.
For each package we fetch the PyPI sdist, take its real SHA256, and inspect
it to see whether it ships a setup.py (haikuports' house INSTALL idiom) or is
PEP 517-only (which needs a wheel-based INSTALL instead).
"""
import hashlib, io, json, os, re, tarfile, urllib.request, zipfile

# pypi name -> (version, haiku package name, extra REQUIRES lines)
PKGS = [
    ("cssutils",        "2.15.0", "cssutils",        []),
    ("edge-tts",        "7.2.8",  "edge_tts",        ["aiohttp_$pythonPackage", "certifi_$pythonPackage", "tabulate_$pythonPackage"]),
    ("encutils",        "1.0.0",  "encutils",        []),
    ("gTTS",            "2.5.4",  "gtts",            ["requests_$pythonPackage", "click_$pythonPackage"]),
    ("more-itertools",  "11.1.0", "more_itertools",  []),
    ("platformdirs",    "4.11.8", "platformdirs",    []),
    ("pycaption",       "2.3.9",  "pycaption",       ["beautifulsoup4_$pythonPackage", "lxml_$pythonPackage",
                                                      "cssutils_$pythonPackage", "python_i18n_$pythonPackage"]),
    ("pysubs2",         "1.8.1",  "pysubs2",         []),
    ("python-docx",     "1.2.0",  "python_docx",     ["lxml_$pythonPackage", "typing_extensions_$pythonPackage"]),
    ("python-i18n",     "0.3.9",  "python_i18n",     []),
    ("sounddevice",     "0.5.6",  "sounddevice",     ["cffi_$pythonPackage", "lib:libportaudio"]),
    ("soundfile",       "0.14.0", "soundfile",       ["cffi_$pythonPackage", "lib:libsndfile"]),
    ("tabulate",        "0.10.0", "tabulate",        []),
]

# Packages whose setup.py declares setup_requires: without these in
# BUILD_REQUIRES setuptools tries to pip-fetch them, and pip is not available
# inside the haikuporter chroot.
# Optional BUILD() bodies for sources that need a nudge before setup.py runs.
BUILD_BODY = {}

BUILD_EXTRA = {
    "soundfile":   ["cffi_$pythonPackage"],
    "sounddevice": ["cffi_$pythonPackage"],
}

# SPDX-ish -> haikuports licenses/ names
LICENSE_OVERRIDE = {
    "cssutils": "GNU LGPL v3", "encutils": "GNU LGPL v3",
    "more_itertools": "MIT",
    "sounddevice": "MIT", "tabulate": "MIT",
    "edge_tts": "GNU GPL v3",
}
LICENSE_MAP = {
    "MIT": "MIT", "MIT License": "MIT",
    "BSD": "BSD (3-clause)", "BSD License": "BSD (3-clause)",
    "BSD-3-Clause": "BSD (3-clause)", "BSD-2-Clause": "BSD (2-clause)",
    "Apache 2.0": "Apache v2", "Apache-2.0": "Apache v2",
    "Apache Software License": "Apache v2",
    "LGPL": "GNU LGPL v3", "GPL": "GNU GPL v3",
    "GNU Lesser General Public License v3 (LGPLv3)": "GNU LGPL v3",
    "MPL-2.0": "MPL v2.0", "MPL 2.0": "MPL v2.0",
}



# Curated text: scraped PyPI long-descriptions are full of badges and code
# blocks, and haikuports requires DESCRIPTION to differ from SUMMARY.
META = {
 "cssutils": ("A CSS Cascading Style Sheets library for Python",
   "Parses CSS into a DOM-like object model that can be inspected and modified, then serialised back out to CSS text. It follows the W3C CSSOM specifications."),
 "edge_tts": ("Use Microsoft Edge's online text-to-speech service from Python",
   "Lets Python code synthesise speech through Microsoft Edge's online text-to-speech service, without installing the browser or holding an API key. Audio can be written to a file or streamed."),
 "encutils": ("Detect the character encoding of HTTP, XML and HTML content",
   "Determines the character encoding of text retrieved over HTTP or contained in XML and HTML documents, following the encoding rules those specifications define."),
 "gtts": ("Python interface to Google Translate's text-to-speech API",
   "Provides a library and command line tool that turn text into spoken audio using Google Translate's text-to-speech service, writing the result to a file or stream."),
 "more_itertools": ("Additional building blocks for working with Python iterables",
   "A collection of routines for working with iterables that complements the standard library itertools module, covering grouping, windowing, chunking and related operations."),
 "platformdirs": ("Determine platform-specific directories for app data and config",
   "Works out the correct per-platform locations for application data, configuration, cache, state and log directories, so applications do not have to encode those conventions themselves."),
 "pycaption": ("Read, write and convert caption and subtitle formats",
   "Reads and writes caption and subtitle formats including SRT, WebVTT, DFXP/TTML and SAMI, and converts between them while preserving timing and styling where the formats allow."),
 "pysubs2": ("Load, edit and save subtitle files in several formats",
   "A library and command line tool for loading, editing and saving subtitle files in the SubStation Alpha, SubRip, WebVTT and MicroDVD formats, including retiming and format conversion."),
 "python_docx": ("Create and update Microsoft Word .docx files from Python",
   "Creates, reads and modifies Microsoft Word .docx documents, giving programmatic access to paragraphs, runs, styles, tables and inline images."),
 "python_i18n": ("Lightweight internationalisation library using YAML or JSON",
   "A small internationalisation and localisation library that loads translations from YAML or JSON files and resolves them by locale, with placeholder interpolation and pluralisation."),
 "sounddevice": ("Play and record sound using PortAudio and NumPy arrays",
   "Binds the PortAudio library so that sound can be played and recorded directly from NumPy arrays, with both blocking and callback-driven streaming interfaces."),
 "soundfile": ("Read and write sound files through libsndfile",
   "Reads and writes sound files in the many formats libsndfile supports, returning and accepting audio as NumPy arrays, with support for seeking and block-wise processing."),
 "tabulate": ("Format tabular data as plain-text tables",
   "Renders lists, dictionaries and NumPy arrays as readable plain-text tables in a range of styles, including grid, pipe, rst and HTML output."),
}


def author_of(info):
    """PyPI puts the author in `author`, or as "Name <mail>" in `author_email`."""
    for key in ("author", "maintainer"):
        v = (info.get(key) or "").strip()
        if v and "@" not in v:
            return v
    for key in ("author_email", "maintainer_email"):
        v = (info.get(key) or "").strip()
        if "<" in v:
            name = v.split("<", 1)[0].strip().strip('"')
            if name:
                return name
        elif v and "@" not in v:
            return v
    return "FIXME-check-upstream-LICENSE"

def clean(t):
    """Strip characters that bash would interpret inside a double-quoted string."""
    t = (t or "").replace("`", "'").replace('"', "'").replace("\\", "/").replace("$", "")
    return re.sub(r"\s+", " ", t).strip()

def make_summary(raw, hname):
    t = clean(raw).rstrip(".")
    # haikuports policy: must not begin with the port name
    if t.lower().startswith(hname.lower()):
        t = t[len(hname):].lstrip(" :-,")
        t = t[0].upper() + t[1:] if t else t
    if not t:
        t = "Python module"
    if len(t) > 80:                      # policy: 80 chars max
        t = t[:77].rsplit(" ", 1)[0] + "..."
    return t

def make_description(long_desc, summary, homepage, pypi_name):
    """DESCRIPTION must differ from SUMMARY, so build a real paragraph."""
    body = clean(long_desc)
    body = re.sub(r"^[=#*\-_]{3,}", " ", body)
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", body)   # images
    body = re.sub(r"https?://\S+", "", body)              # badge urls
    body = re.sub(r"\s+", " ", body).strip()
    sentences = re.split(r"(?<=[.!?]) ", body)
    picked = " ".join(sentences[:4]).strip()
    if len(picked) < 40 or picked.lower().rstrip(".") == summary.lower().rstrip("."):
        picked = (f"{summary}. This port packages the {pypi_name} module for "
                  f"Python on Haiku.")
    if len(picked) > 600:
        picked = picked[:597].rsplit(" ", 1)[0] + "..."
    return picked

def pypi(name, version):
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    with urllib.request.urlopen(url, timeout=40) as r:
        return json.load(r)

def sdist_of(meta):
    for u in meta["urls"]:
        if u["packagetype"] == "sdist":
            return u
    return None

def has_setup_py(blob, filename):
    bio = io.BytesIO(blob)
    try:
        if filename.endswith(".zip"):
            with zipfile.ZipFile(bio) as z:
                names = z.namelist()
        else:
            with tarfile.open(fileobj=bio) as t:
                names = t.getnames()
    except Exception:
        return None
    return any(n.count("/") == 1 and n.endswith("/setup.py") for n in names)

def wrap(text, width=78):
    out, line = [], ""
    for w in (text or "").split():
        if len(line) + len(w) + 1 > width:
            out.append(line); line = w
        else:
            line = (line + " " + w).strip()
    if line: out.append(line)
    return " \\\n".join(out) if out else "See the project homepage."

TEMPLATE = """SUMMARY="{summary}"
DESCRIPTION="{description}"
HOMEPAGE="{homepage}"
COPYRIGHT="{copyright}"
LICENSE="{license}"
REVISION="1"
SOURCE_URI="{source_uri}"
CHECKSUM_SHA256="{sha256}"
{source_dir_line}
ARCHITECTURES="any"

PROVIDES="
\t$portName = $portVersion
\t"
REQUIRES="
\thaiku
\t"

BUILD_REQUIRES="
\thaiku_devel
\t"

PYTHON_VERSIONS=(3.10)

for pythonVersion in ${{PYTHON_VERSIONS[@]}}; do
\tpythonPackage=python${{pythonVersion//.}}

\teval "PROVIDES_$pythonPackage=\\"
\t\t{hname}_$pythonPackage = $portVersion
\t\t\\""
\teval "REQUIRES_$pythonPackage=\\"
\t\thaiku
\t\tcmd:python$pythonVersion
{extra_requires}\t\t\\""

\tBUILD_REQUIRES+="
\t\tsetuptools_$pythonPackage
{build_extra}\t\t"
\tBUILD_PREREQUIRES+="
\t\tcmd:python$pythonVersion
\t\t"
done

{build_section}INSTALL()
{{
\tfor pythonVersion in ${{PYTHON_VERSIONS[@]}}; do
\t\tpythonPackage=python${{pythonVersion//.}}

\t\tpython=python$pythonVersion
\t\tinstallLocation=$prefix/lib/$python/vendor-packages/
\t\texport PYTHONPATH=$installLocation:$PYTHONPATH

\t\tmkdir -p $installLocation
\t\trm -rf build

{install_body}
\t\tpackageEntries $pythonPackage \\
\t\t\t$prefix/lib/python*
\tdone
}}
"""

SETUPPY_BODY = """\t\t$python setup.py build install \\
\t\t\t--root=/ --prefix=$prefix
"""

# PEP 517-only: build a wheel with the declared backend, then unpack it.
PEP517_BODY = """\t\t# Upstream ships no setup.py (PEP 517-only) and its build backend
\t\t# (hatchling/flit) is not packaged for Haiku yet. This is a pure-Python
\t\t# wheel, which haikuporter has already unpacked, so install its contents
\t\t# directly - they include the .dist-info that importlib.metadata reads.
\t\tcp -R ./* $installLocation/
"""

def main():
    rows = []
    for pypi_name, version, hname, extra in PKGS:
        meta = pypi(pypi_name, version)
        info, sd = meta["info"], sdist_of(meta)
        if sd is None:
            rows.append((hname, version, "NO SDIST", "-")); continue
        blob = urllib.request.urlopen(sd["url"], timeout=90).read()
        sha = hashlib.sha256(blob).hexdigest()
        assert sha == sd["digests"]["sha256"], f"{pypi_name}: checksum mismatch vs PyPI"
        setuppy = has_setup_py(blob, sd["filename"])
        if not setuppy:
            whl = next((u for u in meta["urls"]
                        if u["packagetype"] == "bdist_wheel"
                        and u["filename"].endswith("py3-none-any.whl")), None)
            if whl is None:
                rows.append((hname, version, "NO PURE WHEEL", "-")); continue
            sd = whl
            sha = whl["digests"]["sha256"]
        lic = info.get("license") or ""
        classifiers = [c for c in info.get("classifiers", []) if c.startswith("License ::")]
        if classifiers:
            lic = classifiers[-1].split("::")[-1].strip()
        hlic = LICENSE_OVERRIDE.get(hname) or LICENSE_MAP.get(lic.strip(), lic.strip() or "FIXME")
        if sd["filename"].endswith(".whl"):
            src_dir = None         # no single top-level dir inside a wheel
        else:
            src_dir = sd["filename"]
            for suf in (".tar.gz", ".zip", ".tar.bz2"):
                if src_dir.endswith(suf): src_dir = src_dir[:-len(suf)]
        extra_lines = "".join(f"\t\t{e}\n" for e in extra)
        body = SETUPPY_BODY if setuppy else PEP517_BODY
        curated = META.get(hname)
        summary = make_summary(curated[0] if curated else (info.get("summary") or hname), hname)
        text = TEMPLATE.format(
            summary=summary,
            description=wrap(curated[1] if curated
                             else make_description(info.get("description"), summary,
                                                   info.get("home_page") or "", pypi_name)),
            homepage=info.get("home_page") or info.get("project_url") or f"https://pypi.org/project/{pypi_name}/",
            copyright=clean(f"{(sd.get('upload_time') or '2026')[:4]} {author_of(info)}"),
            license=hlic,
            source_uri=sd["url"],
            sha256=sha,
            # An empty SOURCE_DIR tells haikuporter the archive has no single
            # top-level directory to descend into (true for wheels).
            source_dir_line=('SOURCE_DIR=""\n' if src_dir is None
                             else f'SOURCE_DIR="{src_dir}"\n'),
            hname=hname,
            extra_requires=extra_lines,
            install_body=body,
            build_section=("BUILD()\n{\n" + BUILD_BODY[hname] + "}\n\n"
                           if hname in BUILD_BODY else ""),
            build_extra="".join(f"\t\t{b}\n" for b in BUILD_EXTRA.get(hname, [])),
        )
        d = os.path.join("dev-python", hname)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{hname}-{version}.recipe"), "w") as f:
            f.write(text)
        rows.append((hname, version, "setup.py" if setuppy else "PEP517-only", hlic))
    print(f"{'package':20s} {'version':10s} {'build':14s} license")
    print("-" * 62)
    for r in rows:
        print(f"{r[0]:20s} {r[1]:10s} {r[2]:14s} {r[3]}")

main()
