# Pending in HaikuPorts

Python 3.14 versions of HaikuPorts recipes that Subtitld needs, from the pull
request that adds them there (branch `python314-subtitld-deps` of
<https://github.com/Subtitld/haikuports>): typing_extensions, click,
pycparser, cffi (2.1.1), soupsieve (2.10), beautifulsoup4, and hatchling with
pathspec and editables, which beautifulsoup4 and soupsieve build with.

Until HaikuPorts has them, CI's Haiku build (`.github/scripts/haiku-build.sh`)
builds them first, in `build-order`, and they are not part of the ports
submitted for Subtitld (`../dev-python`, `../media-video`). Once they are
merged there, delete this folder.
