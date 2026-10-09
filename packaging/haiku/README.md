# Haiku packaging for Subtitld

A haikuports recipe tree for Subtitld and the Python packages it needs that
Haiku does not package yet.

    media-video/subtitld/subtitld-26.09.recipe   the application
    dev-python/<name>/<name>-<version>.recipe    15 dependencies
    gen_recipes.py                               regenerates the dev-python recipes

## Built by CI

The Build workflow (`.github/workflows/build.yml`, job `haiku`) runs
`.github/scripts/haiku-build.sh` in a Haiku R1/beta6 VM: it lints the tree,
builds all 16 recipes with haikuporter, installs the packages with `pkgman`
and checks the app's modules import. No release is needed: the app's recipe
is renamed to the build's version and pointed at a tarball of the checkout,
shaped like GitHub's tag archive and served from localhost.

The same script runs locally in the image CI uses
([haiku-builder](https://github.com/cross-platform-actions/haiku-builder)
releases, a qcow2 for QEMU; log in over SSH as `user`, no password).

On a stable release, `release.yml` attaches the app's recipe to the GitHub
release, pointed at the tag with its checksum: that is the file for
haikuports.

## Dependencies

Already in haikuports, so no recipe needed: pyside6, numpy, scipy, lxml, cffi,
pillow, requests, aiohttp, certifi, chardet, beautifulsoup4, click, soupsieve,
typing_extensions, opencv_python3.10.

Also required at runtime: **`cmd:ffmpeg`**, which comes from the `ffmpeg_tools`
package — *not* `ffmpeg`, which ships libraries only. Subtitld shells out to the
ffmpeg binary through `session.FFMPEG_EXECUTABLE`, so this matters.

Dropped on Haiku:

* `PySideSix-Frameless-Window` — `__main__.py` only imports it off-Haiku, so the
  package does not need it. Verified by deleting the module and relaunching.
* `mediapipe` — no Haiku build is realistic (Bazel, TFLite, XNNPACK, binary
  wheels only). Guarded at its import sites; face detection degrades to an
  error message.
* `pywhispercpp` — not built for Haiku yet. Already guarded upstream. whisper.cpp
  is portable C++, so this one is a plausible future port.

## Two INSTALL templates

Nine dependencies ship no `setup.py` (PEP 517-only) and their build backends
(hatchling, flit) are not packaged for Haiku. Those recipes fetch the
`py3-none-any` wheel and install its unpacked contents, which keeps the
`.dist-info` that `importlib.metadata` reads. They set `SOURCE_DIR=""`, because a
wheel has no single top-level directory and haikuporter otherwise looks for
`<port>-<version>/`.

The other six use the house `setup.py build install` idiom. Two of them
(`soundfile`, `sounddevice`) declare `setup_requires`, so `cffi` is listed in
`BUILD_REQUIRES`; otherwise setuptools tries to pip-fetch it, and pip does not
exist inside the chroot.

A reviewer may prefer sdists over wheels. Building the nine from source means
packaging hatchling and flit_core for Haiku first.

## Building it yourself

    pkgman install haikuporter
    git clone https://github.com/haikuports/haikuports.git
    cp -r dev-python/* media-video/* haikuports/    # merge this tree in
    cd haikuports && haikuporter --lint
    haikuporter -y subtitld

To build in a throwaway tree instead of a full haikuports checkout, a minimal
tree needs `haikuports.conf` (with `PACKAGER`, `TREE_PATH`,
`TARGET_ARCHITECTURE`), a `licenses/` directory, and a `FormatVersions` file
containing `RecipeFormatVersion=1`; `.github/scripts/haiku-build.sh` sets one
up.

## Submitting

haikuports takes contributions as pull requests against
<https://github.com/haikuports/haikuports>. Each package is its own commit
(`dev-python/foo: new recipe`). Run `haikuporter --lint` before opening the PR.
The dependency recipes are independently useful and can go first; several
(`platformdirs`, `more_itertools`, `tabulate`, `soundfile`, `sounddevice`) are
common enough that other ports will want them.

## Known rough edges

* Versions are pinned to exactly what was verified working in the VM. Bump them
  deliberately rather than tracking latest.
* `TEST()` in the subtitld recipe does a headless import check, but
  haikuporter runs it in the build chroot, where neither the app nor its
  runtime dependencies are installed, so it fails there. CI does the same
  check after installing the packages instead.
