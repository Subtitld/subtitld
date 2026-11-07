#!/usr/bin/env python3
"""Write the haikuports recipes for the Python packages Subtitld needs and
Haiku does not package yet (dev-python/).

    python3 gen_recipes.py        (from packaging/haiku)

They follow haikuports' own pure-Python recipes (iniconfig, certifi): PyPI's
wheel, repackaged with installer, so nothing is built and no build backend is
needed; the package is the Python one alone, with no empty base package. Each
wheel's checksum is taken from PyPI and checked against the download.
Copyright lines and licenses come from each project's own LICENSE file; check
them again on a version bump.
"""
import hashlib
import json
import os
import urllib.request

PKGS = [
    dict(port="gtts", pypi="gTTS", version="2.5.4", commands=["gtts-cli"],
         summary="Python interface to Google Translate's text-to-speech API",
         description="Provides a library and command line tool that turn text into spoken "
                     "audio using Google Translate's text-to-speech service, writing the result "
                     "to a file or stream.",
         homepage="https://github.com/pndurette/gTTS",
         copyright=["2014-2024 Pierre Nicolas Durette"],
         license=["MIT"],
         requires=["click", "requests"]),
    # hatch-vcs is not packaged for Haiku: the wheel instead.
    dict(port="platformdirs", pypi="platformdirs", version="4.11.8",
         summary="Determine platform-specific directories for app data and config",
         description="Works out the correct per-platform locations for application data, "
                     "configuration, cache, state and log directories, so applications do not "
                     "have to encode those conventions themselves.",
         homepage="https://github.com/tox-dev/platformdirs",
         copyright=["2010-202x The platformdirs developers"],
         license=["MIT"],
         requires=[]),
    dict(port="pysubs2", pypi="pysubs2", version="1.8.1", commands=["pysubs2"],
         summary="Load, edit and save subtitle files in several formats",
         description="A library and command line tool for loading, editing and saving subtitle "
                     "files in the SubStation Alpha, SubRip, WebVTT and MicroDVD formats, "
                     "including retiming and format conversion.",
         homepage="https://github.com/tkarabela/pysubs2",
         copyright=["2014-2026 Tomas Karabela"],
         license=["MIT"],
         requires=[]),
    dict(port="python_docx", pypi="python-docx", version="1.2.0",
         summary="Create and update Microsoft Word .docx files from Python",
         description="Creates, reads and modifies Microsoft Word .docx documents, giving "
                     "programmatic access to paragraphs, runs, styles, tables and inline images.",
         homepage="https://github.com/python-openxml/python-docx",
         copyright=["2013 Steve Canny"],
         license=["MIT"],
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
         requires=[]),
    dict(port="sounddevice", pypi="sounddevice", version="0.5.6",
         summary="Play and record sound using PortAudio and NumPy arrays",
         description="Binds the PortAudio library so that sound can be played and recorded "
                     "directly from NumPy arrays, with both blocking and callback-driven "
                     "streaming interfaces.",
         homepage="https://github.com/spatialaudio/python-sounddevice",
         copyright=["2015-2025 Matthias Geier"],
         license=["MIT"],
         requires=["cffi", "lib:libportaudio"]),
    dict(port="soundfile", pypi="soundfile", version="0.14.0",
         summary="Read and write sound files through libsndfile",
         description="Reads and writes sound files in the many formats libsndfile supports, "
                     "returning and accepting audio as NumPy arrays, with support for seeking "
                     "and block-wise processing.",
         homepage="https://github.com/bastibe/python-soundfile",
         copyright=["2013 Bastian Bechtold"],
         license=["BSD (3-clause)"],
         requires=["cffi", "numpy", "typing_extensions", "lib:libsndfile"]),
]

HEADER = """SUMMARY="{summary}"
DESCRIPTION="{description}"
HOMEPAGE="{homepage}"
COPYRIGHT="{copyright}"
LICENSE="{license}"
REVISION="1"
SOURCE_URI="{source_uri}"
CHECKSUM_SHA256="{sha256}"

ARCHITECTURES="any"

BUILD_REQUIRES="
\thaiku_devel
\t"

PYTHON_VERSIONS=(3.14)

for pythonVersion in ${{PYTHON_VERSIONS[@]}}; do
\tpythonPackage=python${{pythonVersion//.}}

\t# Allows using dot in sub-package names (eg: "_python3.14" vs "_python314"):
\teval "PACKAGE_NAME_$pythonPackage=${{portName}}_python$pythonVersion"

\teval "PROVIDES_$pythonPackage=\\"
\t\t${{portName}}_python$pythonVersion = $portVersion
\t\t${{portName}}_$pythonPackage = $portVersion
{commands}\t\t\\""
\teval "REQUIRES_$pythonPackage=\\"
\t\thaiku
{requires}\t\tcmd:python$pythonVersion
\t\t\\""
\tBUILD_REQUIRES+="
\t\tinstaller_$pythonPackage
\t\t"
\tBUILD_PREREQUIRES+="
\t\tcmd:python$pythonVersion
\t\t"
done

INSTALL()
{{
\tfor pythonVersion in ${{PYTHON_VERSIONS[@]}}; do
\t\tpythonPackage=python${{pythonVersion//.}}

\t\tpython=python$pythonVersion

\t\t$python -m installer -p $prefix {wheel}

\t\tpackageEntries $pythonPackage \\
\t\t\t$prefix/lib/python*{bin_dir}
\tdone
}}
"""

def pypi_file(name, version):
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=40) as r:
        meta = json.load(r)
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
    f = pypi_file(p["pypi"], p["version"])
    # PyPI's stable path for a wheel: /packages/<python tag>/<initial>/<project>/<file>,
    # written as HaikuPorts' other wheel recipes write it when the wheel is named
    # after the port.
    if f["filename"] == f"{p['port']}-{p['version']}-py3-none-any.whl":
        filename = "$portName-$portVersion-py3-none-any.whl"
        uri = f"https://files.pythonhosted.org/packages/py3/${{portName:0:1}}/$portName/{filename}#noarchive"
    else:
        filename = f["filename"].replace(p["version"], "$portVersion")
        tag = f["filename"].split("-")[-3]
        uri = f"https://files.pythonhosted.org/packages/{tag}/{p['pypi'][0]}/{p['pypi']}/{filename}#noarchive"
    url = (uri.split("#")[0].replace("${portName:0:1}", p["port"][0])
           .replace("$portName", p["port"]).replace("$portVersion", p["version"]))
    sha = checked_sha256(url, f["digests"]["sha256"])

    python_deps = sorted(r for r in p["requires"] if ":" not in r)
    other_deps = sorted(r for r in p["requires"] if ":" in r)
    requires = "".join(f"\t\t{r}_$pythonPackage\n" for r in python_deps)
    requires += "".join(f"\t\t{r}\n" for r in other_deps)
    # Commands the wheel installs go with the package: cmd:gtts_cli for gtts-cli.
    commands = "".join(f"\t\tcmd:{c.replace('-', '_')} = $portVersion\n" for c in p.get("commands", []))
    return HEADER.format(
        summary=p["summary"],
        description=wrap(p["description"]),
        homepage=p["homepage"],
        copyright="\n\t".join(p["copyright"]),
        license="\n\t".join(p["license"]),
        source_uri=uri,
        sha256=sha,
        requires=requires,
        commands=commands,
        wheel=filename,
        bin_dir=" \\\n\t\t\t$binDir" if p.get("commands") else "",
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
        print(f"{p['port']:14s} {p['version']:9s} {', '.join(p['license'])}")


main()
