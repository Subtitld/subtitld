#!/bin/sh
# Build the MXF checkers test_corpus_mxf runs, when they are there:
# asdcplib (CineCert, the SMPTE DCP reference) and bmx (BBC, IMF and
# broadcast MXF). Neither is packaged for most systems.
#
#   sh tests/corpus/build_tools.sh        # into ~/.cache/subtitld/tools
#   SUBTITLD_TOOLS=/elsewhere sh tests/corpus/build_tools.sh
#
# Needs cmake, a C++ compiler and the headers of OpenSSL, Xerces-C, Expat
# and uriparser. CMake 4 refuses their old minimum versions without the
# policy flag; GCC 14 refuses an asdcplib template without -Wno-template-body.
set -e
TOOLS=${SUBTITLD_TOOLS:-$HOME/.cache/subtitld/tools}
mkdir -p "$TOOLS/src"
cd "$TOOLS/src"
[ -d asdcplib ] || git clone -q --depth 1 -b rel_2_10_35 https://github.com/cinecert/asdcplib.git
[ -d bmx ] || git clone -q --depth 1 -b v1.6 https://github.com/bbc/bmx.git
for project in asdcplib bmx; do
    cmake -S $project -B $project/build -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_CXX_FLAGS=-Wno-template-body \
        -DCMAKE_INSTALL_PREFIX="$TOOLS" -DCMAKE_BUILD_TYPE=Release -DBMX_BUILD_TESTING=OFF > /dev/null
    cmake --build $project/build -j"$(nproc)" > /dev/null
    cmake --install $project/build > /dev/null
done
echo "built into $TOOLS/bin"
