#!/usr/bin/env bash
# Build libdatachannel (live co-editing's peer-to-peer media transfer) and libopus (its voice call) for the macOS package: data channels only
# (no media, no websocket), with Mbed TLS linked in, for the machine's architecture (Apple Silicon on CI).
#
#   packaging/libdatachannel/build-macos.sh            # → packaging/libdatachannel/dist/macos-arm64/
#
# Needs Xcode's command-line tools, CMake and Ninja. Same versions and licences as build-linux.sh.
set -euo pipefail

LIBDATACHANNEL=v0.23.2
MBEDTLS=v3.6.4
OPUS=1.6.1
OPUS_SHA256=6ffcb593207be92584df15b32466ed64bbec99109f007c82205f0194572411a1
ARCH=$(uname -m)
HERE=$(cd "$(dirname "$0")" && pwd)
OUT="$HERE/dist/macos-$ARCH"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
export MACOSX_DEPLOYMENT_TARGET=${MACOSX_DEPLOYMENT_TARGET:-11.0}
mkdir -p "$OUT/licenses"
cd "$WORK"

git clone -q --depth 1 --branch $MBEDTLS --recurse-submodules --shallow-submodules https://github.com/Mbed-TLS/mbedtls.git
# libdatachannel's DTLS asks Mbed TLS for DTLS-SRTP, which is off by default.
python3 mbedtls/scripts/config.py -f mbedtls/include/mbedtls/mbedtls_config.h set MBEDTLS_SSL_DTLS_SRTP
cmake -S mbedtls -B mbedtls/build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
  -DENABLE_TESTING=OFF -DENABLE_PROGRAMS=OFF -DUSE_SHARED_MBEDTLS_LIBRARY=OFF -DUSE_STATIC_MBEDTLS_LIBRARY=ON \
  -DCMAKE_INSTALL_PREFIX="$WORK/mbedtls-install"
cmake --build mbedtls/build && cmake --install mbedtls/build

# Static Mbed TLS: its x509 and crypto libraries go on the link line too (see build-linux.sh).
git clone -q --depth 1 --branch $LIBDATACHANNEL --recurse-submodules --shallow-submodules https://github.com/paullouisageneau/libdatachannel.git
# libjuice draws its ICE credentials from the system's random source only on Linux and Windows.
patch -d libdatachannel/deps/libjuice -p1 < "$HERE/libjuice-urandom.patch"
cmake -S libdatachannel -B libdatachannel/build -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DNO_MEDIA=ON -DNO_WEBSOCKET=ON -DNO_EXAMPLES=ON -DNO_TESTS=ON -DUSE_MBEDTLS=ON \
  -DCMAKE_PREFIX_PATH="$WORK/mbedtls-install" -DMbedTLS_ROOT="$WORK/mbedtls-install" \
  -DCMAKE_CXX_STANDARD_LIBRARIES="$WORK/mbedtls-install/lib/libmbedx509.a $WORK/mbedtls-install/lib/libmbedcrypto.a"
cmake --build libdatachannel/build
cp -L libdatachannel/build/libdatachannel.dylib "$OUT/libdatachannel.dylib"
# Found by name next to the app, not through an install path.
install_name_tool -id @rpath/libdatachannel.dylib "$OUT/libdatachannel.dylib"
strip -x "$OUT/libdatachannel.dylib"
codesign --force --sign - "$OUT/libdatachannel.dylib"
cp libdatachannel/LICENSE "$OUT/licenses/libdatachannel.txt"
cp mbedtls/LICENSE "$OUT/licenses/mbedtls.txt"
cp libdatachannel/deps/libjuice/LICENSE "$OUT/licenses/libjuice.txt"
cp libdatachannel/deps/usrsctp/LICENSE.md "$OUT/licenses/usrsctp.txt"

# libopus, for the voice call (see build-linux.sh).
curl -sfLO "https://downloads.xiph.org/releases/opus/opus-$OPUS.tar.gz"
echo "$OPUS_SHA256  opus-$OPUS.tar.gz" | shasum -a 256 -c - > /dev/null
tar -xzf "opus-$OPUS.tar.gz"
cmake -S "opus-$OPUS" -B opus-build -G Ninja -DCMAKE_BUILD_TYPE=Release -DOPUS_BUILD_SHARED_LIBRARY=ON \
  -DOPUS_BUILD_PROGRAMS=OFF -DOPUS_BUILD_TESTING=OFF -DOPUS_INSTALL_PKG_CONFIG_MODULE=OFF
cmake --build opus-build
cp -L opus-build/libopus.0.dylib "$OUT/libopus.0.dylib"
install_name_tool -id @rpath/libopus.0.dylib "$OUT/libopus.0.dylib"
strip -x "$OUT/libopus.0.dylib"
codesign --force --sign - "$OUT/libopus.0.dylib"
cp "opus-$OPUS/COPYING" "$OUT/licenses/opus.txt"

echo "libdatachannel $LIBDATACHANNEL, Mbed TLS $MBEDTLS, Opus $OPUS, macOS $MACOSX_DEPLOYMENT_TARGET+ $ARCH" > "$OUT/VERSION"
otool -L "$OUT/libdatachannel.dylib" "$OUT/libopus.0.dylib"
ls -la "$OUT"
