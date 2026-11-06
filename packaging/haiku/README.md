# Haiku packaging for Subtitld

A haikuports recipe tree for Subtitld and the Python packages it needs that
Haiku does not package yet.

    media-video/subtitld/subtitld-26.10.recipe   the application
    media-video/subtitld/additional-files/       its attributes, file type and icon
    dev-python/<name>/<name>-<version>.recipe    7 dependencies
    gen_recipes.py                               regenerates the dev-python recipes
    haikudepot/                                  HaikuDepot's listing: screenshots, texts

## A native application

Subtitld is Python, but on Haiku it looks and behaves like a native app:

* `/boot/system/apps/Subtitld` is Subtitld's own Python script, typed as an
  application and carrying Subtitld's signature
  (`application/x-vnd.qt6-Subtitld`, the one Qt gives an application on
  Haiku), version and vector icon as attributes (`subtitld.rdef.in`, compiled
  with `rc` and written with `resattr`, as frescobaldi's recipe does). Started
  from Deskbar or Tracker, it shows there as Subtitld, with its icon; started
  by its path from a Terminal, as python3.14. Tracker, the Deskbar menu and
  HaikuDepot show the icon. HaikuDepot's server takes an app's icon
  from its package when the package holds a single application with one.
* The icon, `subtitld.hvif`, is generated from `packaging/branding/icon.svg`
  by `packaging/branding/render-icons.py`, along with the resources: the
  mark's two shapes in their colours over a darker outline, and its shadow,
  flat (HVIF has no blur).

Projects (`.usfx`) are a file type of their own, with their icon
(`usfx.hvif`): `subtitld-filetypes.sh`, run once on install
(`POST_INSTALL_SCRIPTS`), adds the type and a sniffer rule so Tracker tells a
project from any other zip. Opened from Tracker, a project opens in Subtitld.

## Built by CI

The Build workflow (`.github/workflows/build.yml`, job `haiku`) runs
`.github/scripts/haiku-build.sh` in a Haiku R1/beta6 VM: it lints the tree,
builds all 10 recipes with haikuporter, installs the packages with `pkgman`
and checks the app's modules import. No release is needed: the app's recipe
is renamed to the build's version and pointed at a tarball of the checkout,
shaped like GitHub's tag archive and served from localhost.

The same script runs locally in the image CI uses
([haiku-builder](https://github.com/cross-platform-actions/haiku-builder)
releases, a qcow2 for QEMU; log in over SSH as `user`, no password).

On a stable release, `release.yml` attaches
`subtitld-<version>-haikuports.tar.gz` to the GitHub release: this tree's
ports, with the app's recipe named after the tag (`subtitld-26.10.recipe` for
`v26.10`) and the tag archive's checksum. Unpack it into a haikuports checkout
(`tar -xzf ... -C haikuports`).

## Dependencies

For Python 3.14, already in haikuports: pyside6, numpy, lxml, requests,
certifi, chardet. Given a 3.14 version in a pull request of their own, and kept
in `pending/` until it is merged: typing_extensions, click (8.1.8), pycparser
(2.23), soupsieve (2.10), beautifulsoup4 and cffi (2.1.1). Left out on Haiku,
as Subtitld runs without them: OpenCV and scipy (no 3.14 build there; face
detection needs MediaPipe anyway, which Haiku lacks) and edge-tts (aiohttp and
its own dependencies have none either), so Edge's voices are not offered
there.

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

## How the dependencies are built

As haikuports packages its pure-Python modules now (iniconfig, certifi): PyPI's
wheel, repackaged with `installer` (`#noarchive`), so nothing is built and no
build backend is needed. Each recipe makes the Python 3.14 package alone, with
no empty base package; the commands a wheel installs (`gtts-cli`, `pysubs2`) go
with it.

`gen_recipes.py` writes them: each archive's checksum comes from PyPI and is
checked against the download, and each recipe's copyright and license lines
come from the project's own LICENSE file. Check those again on a version bump.

## Building it yourself

    pkgman install haikuporter
    git clone https://github.com/haikuports/haikuports.git
    cp -r dev-python media-video haikuports/    # merge this tree in
    cd haikuports && haikuporter --lint
    haikuporter -y subtitld

To build in a throwaway tree instead of a full haikuports checkout, a minimal
tree needs `haikuports.conf` (with `PACKAGER`, `TREE_PATH`,
`TARGET_ARCHITECTURE`), a `licenses/` directory, and a `FormatVersions` file
containing `RecipeFormatVersion=1`; `.github/scripts/haiku-build.sh` sets one
up.

## Submitting

haikuports takes contributions as pull requests against
<https://github.com/haikuports/haikuports>, from a branch of your fork. Each
package is its own commit, as in `pysubs2, new recipe`. Run
`haikuporter --lint` before opening the PR. Its template asks you to confirm
that you built the recipes on your own Haiku machine and that their license
and copyright lines are right.
The dependency recipes are independently useful and can go first; several
(`platformdirs`, `soundfile`, `sounddevice`) are common enough
that other ports will want them.

## Known rough edges

* Versions are pinned to exactly what was verified working in the VM. Bump them
  deliberately rather than tracking latest.
* `TEST()` in the subtitld recipe does a headless import check, but
  haikuporter runs it in the build chroot, where neither the app nor its
  runtime dependencies are installed, so it fails there. CI does the same
  check after installing the packages instead.
