# Pending in HaikuPorts

Python 3.14 versions of HaikuPorts recipes that Subtitld needs, from the pull
request that adds them there (branch `python314-subtitld-deps` of
<https://github.com/Subtitld/haikuports>, pull request #15047 there):
typing_extensions, click (8.1.8), pycparser (2.23), soupsieve (2.10),
beautifulsoup4 and cffi (2.1.1).

Until HaikuPorts has them, CI's Haiku build (`.github/scripts/haiku-build.sh`)
builds them first, in `build-order`, and they are not part of the ports
submitted for Subtitld (`../dev-python`, `../media-video`). Once they are
merged there, delete this folder.
