#!/usr/bin/env bash
# Build libdatachannel (live co-editing's peer-to-peer media transfer) for Linux packages: in the
# manylinux_2_28 container (AlmaLinux 8, glibc 2.28), data channels only (no media, no websocket), with Mbed TLS
# linked in, so the library runs on any newer distribution whatever OpenSSL it has.
#
#   packaging/libdatachannel/build-linux.sh            # → packaging/libdatachannel/dist/linux-x86_64/
#
# Needs Docker. The versions are pinned below; the licences (libdatachannel MPL-2.0, Mbed TLS Apache-2.0,
# libjuice MPL-2.0, usrsctp BSD-3-Clause) are copied next to the library.
set -euo pipefail

LIBDATACHANNEL=v0.23.2
MBEDTLS=v3.6.4
ARCH=${ARCH:-x86_64}
HERE=$(cd "$(dirname "$0")" && pwd)
OUT="$HERE/dist/linux-$ARCH"
mkdir -p "$OUT"

docker run --rm -v "$OUT:/out" "quay.io/pypa/manylinux_2_28_$ARCH" bash -euo pipefail -c "
  dnf install -y -q ninja-build
  cd /tmp
  git clone -q --depth 1 --branch $MBEDTLS --recurse-submodules --shallow-submodules https://github.com/Mbed-TLS/mbedtls.git
  # libdatachannel's DTLS asks Mbed TLS for DTLS-SRTP, which is off by default.
  python3 mbedtls/scripts/config.py -f mbedtls/include/mbedtls/mbedtls_config.h set MBEDTLS_SSL_DTLS_SRTP
  cmake -S mbedtls -B mbedtls/build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DENABLE_TESTING=OFF -DENABLE_PROGRAMS=OFF -DUSE_SHARED_MBEDTLS_LIBRARY=OFF -DUSE_STATIC_MBEDTLS_LIBRARY=ON \
    -DCMAKE_INSTALL_PREFIX=/tmp/mbedtls-install
  cmake --build mbedtls/build -j\$(nproc) && cmake --install mbedtls/build
  # libdatachannel links Mbed TLS's ssl library only, which is enough with shared libraries; static ones need
  # its x509 and crypto too, at the end of the link line. --exclude-libs keeps their symbols to the library.
  git clone -q --depth 1 --branch $LIBDATACHANNEL --recurse-submodules --shallow-submodules https://github.com/paullouisageneau/libdatachannel.git
  cmake -S libdatachannel -B libdatachannel/build -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DNO_MEDIA=ON -DNO_WEBSOCKET=ON -DNO_EXAMPLES=ON -DNO_TESTS=ON -DUSE_MBEDTLS=ON \
    -DCMAKE_PREFIX_PATH=/tmp/mbedtls-install -DMbedTLS_ROOT=/tmp/mbedtls-install \
    -DCMAKE_CXX_STANDARD_LIBRARIES=\"\$(ls /tmp/mbedtls-install/lib*/libmbedx509.a) \$(ls /tmp/mbedtls-install/lib*/libmbedcrypto.a)\" \
    -DCMAKE_SHARED_LINKER_FLAGS=-Wl,--exclude-libs,ALL
  cmake --build libdatachannel/build -j\$(nproc)
  cp -L libdatachannel/build/libdatachannel.so /out/libdatachannel.so
  strip --strip-unneeded /out/libdatachannel.so
  mkdir -p /out/licenses
  cp libdatachannel/LICENSE /out/licenses/libdatachannel.txt
  cp mbedtls/LICENSE /out/licenses/mbedtls.txt
  cp libdatachannel/deps/libjuice/LICENSE /out/licenses/libjuice.txt
  cp libdatachannel/deps/usrsctp/LICENSE.md /out/licenses/usrsctp.txt
  echo 'libdatachannel $LIBDATACHANNEL, Mbed TLS $MBEDTLS, built in manylinux_2_28' > /out/VERSION
  objdump -T /out/libdatachannel.so | grep -o 'GLIBC_[0-9.]*' | sort -Vu | tail -1
  ldd /out/libdatachannel.so
  chown -R $(id -u):$(id -g) /out
"
ls -la "$OUT"
