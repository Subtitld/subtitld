#!/usr/bin/env python3
"""Write the haikuports recipes for the Python packages Subtitld needs and
Haiku does not package yet (dev-python/).

    python3 gen_recipes.py        (from packaging/haiku)

They follow haikuports' own: built from PyPI's source archive with build and
installer (as requests and beautifulsoup4 are), or, where the build backend
is not packaged for Haiku, installed from the pure-Python wheel with installer
(as typing_extensions is). Each archive's checksum is taken from PyPI and
checked against the download. Copyright lines and licenses come from each
project's own LICENSE file; check them again on a version bump.
"""
import hashlib
import json
import os
import urllib.request

PKGS = [
    dict(port="edge_tts", pypi="edge-tts", version="7.3.1",
         summary="Use Microsoft Edge's online text-to-speech service from Python",
         description="Lets Python code synthesise speech through Microsoft Edge's online "
                     "text-to-speech service, without installing the browser or holding an API "
                     "key. Audio can be written to a file or streamed, with subtitles for it.",
         homepage="https://github.com/rany2/edge-tts",
         # LGPLv3, except src/edge_tts/srt_composer.py: MIT.
         copyright=["2014-2023 Christopher Down", "2025 rany"],
         license=["GNU LGPL v3", "MIT"],
         build=["setuptools"],
         requires=["aiohttp", "certifi", "tabulate", "typing_extensions"]),
    dict(port="gtts", pypi="gTTS", version="2.5.4",
         summary="Python interface to Google Translate's text-to-speech API",
         description="Provides a library and command line tool that turn text into spoken "
                     "audio using Google Translate's text-to-speech service, writing the result "
                     "to a file or stream.",
         homepage="https://github.com/pndurette/gTTS",
         copyright=["2014-2024 Pierre Nicolas Durette"],
         license=["MIT"],
         build=["setuptools", "wheel"],
         requires=["click", "requests"]),
    # hatch-vcs is not packaged for Haiku: the wheel instead.
    dict(port="platformdirs", pypi="platformdirs", version="4.11.8", wheel=True,
         summary="Determine platform-specific directories for app data and config",
         description="Works out the correct per-platform locations for application data, "
                     "configuration, cache, state and log directories, so applications do not "
                     "have to encode those conventions themselves.",
         homepage="https://github.com/tox-dev/platformdirs",
         copyright=["2010-202x The platformdirs developers"],
         license=["MIT"],
         requires=[]),
    dict(port="pysubs2", pypi="pysubs2", version="1.8.1",
         summary="Load, edit and save subtitle files in several formats",
         description="A library and command line tool for loading, editing and saving subtitle "
                     "files in the SubStation Alpha, SubRip, WebVTT and MicroDVD formats, "
                     "including retiming and format conversion.",
         homepage="https://github.com/tkarabela/pysubs2",
         copyright=["2014-2026 Tomas Karabela"],
         license=["MIT"],
         build=["hatchling"],
         requires=[]),
    dict(port="python_docx", pypi="python-docx", version="1.2.0",
         summary="Create and update Microsoft Word .docx files from Python",
         description="Creates, reads and modifies Microsoft Word .docx documents, giving "
                     "programmatic access to paragraphs, runs, styles, tables and inline images.",
         homepage="https://github.com/python-openxml/python-docx",
         copyright=["2013 Steve Canny"],
         license=["MIT"],
         build=["setuptools"],
         requires=["lxml", "typing_extensions"]),
    # The sdist has no LICENSE file; the repository's says this.
    dict(port="python_i18n", pypi="python-i18n", version="0.3.9",
         summary="Lightweight internationalisation library using YAML or JSON",
         description="A small internationalisation and localisation library that loads "
                     "translations from YAML or JSON files and resolves them by locale, with "
                     "placeholder interpolation and pluralisation.",
         homepage="https://github.com/tuvistavie/python-i18n",
         copyright=["2012 Daniel Perez"],
         license=["MIT"],
         build=["setuptools"],
         requires=[]),
    dict(port="sounddevice", pypi="sounddevice", version="0.5.6",
         summary="Play and record sound using PortAudio and NumPy arrays",
         description="Binds the PortAudio library so that sound can be played and recorded "
                     "directly from NumPy arrays, with both blocking and callback-driven "
                     "streaming interfaces.",
         homepage="https://github.com/spatialaudio/python-sounddevice",
         copyright=["2015-2025 Matthias Geier"],
         license=["MIT"],
         build=["cffi", "setuptools", "setuptools_scm"],
         requires=["cffi", "lib:libportaudio"]),
    dict(port="soundfile", pypi="soundfile", version="0.14.0",
         summary="Read and write sound files through libsndfile",
         description="Reads and writes sound files in the many formats libsndfile supports, "
                     "returning and accepting audio as NumPy arrays, with support for seeking "
                     "and block-wise processing.",
         homepage="https://github.com/bastibe/python-soundfile",
         copyright=["2013 Bastian Bechtold"],
         license=["BSD (3-clause)"],
         build=["cffi", "setuptools"],
         requires=["cffi", "numpy", "typing_extensions", "lib:libsndfile"]),
    dict(port="tabulate", pypi="tabulate", version="0.10.0",
         summary="Format tabular data as plain-text tables",
         description="Renders lists, dictionaries and NumPy arrays as readable plain-text tables "
                     "in a range of styles, including grid, pipe, rst and HTML output.",
         homepage="https://github.com/astanin/python-tabulate",
         copyright=["2011-2020 Sergey Astanin and contributors"],
         license=["MIT"],
         build=["setuptools", "setuptools_scm"],
         requires=[]),
]

HEADER = """SUMMARY="{summary}"
DESCRIPTION="{description}"
HOMEPAGE="{homepage}"
COPYRIGHT="{copyright}"
LICENSE="{license}"
REVISION="1"
SOURCE_URI="{source_uri}"
CHECKSUM_SHA256="{sha256}"
{source_dir}
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
\t\t${{portName}}_$pythonPackage = $portVersion
\t\t\\""
\teval "REQUIRES_$pythonPackage=\\"
\t\thaiku
{requires}\t\tcmd:python$pythonVersion
\t\t\\""
\tBUILD_REQUIRES+="
{build_requires}\t\t"
\tBUILD_PREREQUIRES+="
\t\tcmd:python$pythonVersion
\t\t"
done

INSTALL()
{{
\tfor pythonVersion in ${{PYTHON_VERSIONS[@]}}; do
\t\tpythonPackage=python${{pythonVersion//.}}

\t\tpython=python$pythonVersion

{install}
\t\tpackageEntries $pythonPackage \\
\t\t\t$prefix/lib/python*
\tdone
}}
"""

SDIST_INSTALL = """\t\t$python -m build --wheel --no-isolation
\t\t$python -m installer -p $prefix dist/*.whl
"""

WHEEL_INSTALL = """\t\t$python -m installer -p $prefix {wheel}
"""


def pypi_file(name, version, kind):
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=40) as r:
        meta = json.load(r)
    if kind == "sdist":
        return next(u for u in meta["urls"] if u["packagetype"] == "sdist")
    return next(u for u in meta["urls"] if u["packagetype"] == "bdist_wheel"
                and u["filename"].endswith("-none-any.whl"))


def checked_sha256(url, expected):
    with urllib.request.urlopen(url, timeout=120) as r:
        sha = hashlib.sha256(r.read()).hexdigest()
    assert sha == expected, f"{url}: {sha} is not PyPI's {expected}"
    return sha


def wrap(text, width=76):
    lines, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return " \\\n".join(lines + [line])


def recipe(p):
    wheel = p.get("wheel", False)
    f = pypi_file(p["pypi"], p["version"], "wheel" if wheel else "sdist")
    filename = f["filename"].replace(p["version"], "$portVersion")
    # PyPI's stable paths: /packages/source/<initial>/<project>/<file> for
    # sources, /packages/<python tag>/<initial>/<project>/<file> for wheels.
    if wheel:
        tag = f["filename"].split("-")[-3]
        uri = f"https://files.pythonhosted.org/packages/{tag}/{p['pypi'][0]}/{p['pypi']}/{filename}#noarchive"
    else:
        uri = f"https://files.pythonhosted.org/packages/source/{p['pypi'][0]}/{p['pypi']}/{filename}"
    sha = checked_sha256(uri.split("#")[0].replace("$portVersion", p["version"]), f["digests"]["sha256"])
    top = f["filename"][: -len(".tar.gz")] if f["filename"].endswith(".tar.gz") else None
    source_dir = ""
    if top and top != f"{p['port']}-{p['version']}":
        source_dir = f'SOURCE_DIR="{top.replace(p["version"], "$portVersion")}"\n'

    python_deps = sorted(r for r in p["requires"] if ":" not in r)
    other_deps = sorted(r for r in p["requires"] if ":" in r)
    requires = "".join(f"\t\t{r}_$pythonPackage\n" for r in python_deps)
    requires += "".join(f"\t\t{r}\n" for r in other_deps)
    build = ["installer"] if wheel else sorted(set(["build", "installer"] + p["build"]))
    build_requires = "".join(f"\t\t{b}_$pythonPackage\n" for b in build)
    install = (WHEEL_INSTALL.format(wheel=filename.split("#")[0]) if wheel else SDIST_INSTALL)
    return HEADER.format(
        summary=p["summary"],
        description=wrap(p["description"]),
        homepage=p["homepage"],
        copyright="\n\t".join(p["copyright"]),
        license="\n\t".join(p["license"]),
        source_uri=uri,
        sha256=sha,
        source_dir=source_dir,
        requires=requires,
        build_requires=build_requires,
        install=install,
    )


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    for p in PKGS:
        d = os.path.join(here, "dev-python", p["port"])
        os.makedirs(d, exist_ok=True)
        for old in os.listdir(d):
            if old.endswith(".recipe"):
                os.remove(os.path.join(d, old))
        with open(os.path.join(d, f"{p['port']}-{p['version']}.recipe"), "w") as f:
            f.write(recipe(p))
        print(f"{p['port']:14s} {p['version']:9s} {'wheel' if p.get('wheel') else 'sdist':6s} "
              f"{', '.join(p['license'])}")


main()
