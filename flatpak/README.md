# Flatpak

`org.subtitld.Subtitld.yml` builds on Flathub's KDE runtime and the PySide
BaseApp (which brings PySide6 and NumPy). FFmpeg is the runtime's, with the
full codec set from its `codecs-extra` extension; PortAudio is built here.

CI builds it on every run of the Build workflow and lints it the way Flathub
does. To build and run it locally:

    flatpak-builder --user --force-clean --install-deps-from=flathub build-dir \
        flatpak/org.subtitld.Subtitld.yml
    flatpak-builder --run build-dir flatpak/org.subtitld.Subtitld.yml subtitld

Build from a clean checkout: the manifest takes the source from the directory,
so a local `.venv` would be copied in too.

## Python dependencies

`python-modules.json` is generated with
[flatpak-pip-generator](https://github.com/flatpak/flatpak-builder-tools/tree/master/pip),
against the SDK so the wheels match its Python. SciPy and OpenCV come as
wheels (building them takes hours); NumPy is dropped afterwards, since the
BaseApp has it.

    flatpak-pip-generator --runtime org.kde.Sdk//6.11 \
        --prefer-wheels scipy,opencv-python-headless,numpy --wheel-arches x86_64 \
        -o flatpak/python-modules \
        PySideSix-Frameless-Window python-i18n python-docx pycaption beautifulsoup4 \
        chardet==6.0.0 pysubs2 autohex platformdirs certifi deep_translator \
        sounddevice soundfile edge-tts gTTS scipy opencv-python-headless

then remove the `numpy-*.whl` sources from the result. The list follows
`dependencies` in `pyproject.toml`, minus PySide6 and NumPy (the BaseApp's),
with the headless OpenCV, and without mediapipe, which is optional and drags
in a second OpenCV and matplotlib.

## Flathub

Flathub builds from its own repository (`flathub/org.subtitld.Subtitld`), with
this manifest's last module pointing at a release tag instead of the
directory. When submitting, ask for the exception in
`flathub-exceptions.json`: home access, because a project refers to its media
by path.

## Screenshots

`screenshots/` holds the metainfo's screenshots, served from this repository's
`master`. They are the real app, captured off-screen at 1920x1080 with the
showcase video's project (Tears of Steel, CC BY 3.0, Blender Foundation);
the capture script is `capture/flathub.py` in the showcase project.
