# Build libdatachannel (live co-editing's peer-to-peer media transfer) and libopus (its voice call) for the
# Windows packages: libdatachannel with data channels only (no media, no websocket) and Mbed TLS linked in; both
# with the C runtime linked statically, so the DLLs need nothing but Windows.
#
#   pwsh packaging/libdatachannel/build-windows.ps1     # → packaging/libdatachannel/dist/windows-x64/
#
# Needs Visual Studio's C++ tools, CMake and Git. Same versions and licences as build-linux.sh.
$ErrorActionPreference = 'Stop'

$LIBDATACHANNEL = 'v0.23.2'
$MBEDTLS = 'v3.6.4'
$OPUS = '1.6.1'
$OPUS_SHA256 = '6ffcb593207be92584df15b32466ed64bbec99109f007c82205f0194572411a1'
$Out = Join-Path $PSScriptRoot 'dist\windows-x64'
$Work = Join-Path ([System.IO.Path]::GetTempPath()) "libdatachannel-$([guid]::NewGuid())"
New-Item -ItemType Directory -Force "$Out\licenses", $Work | Out-Null

function Run { & $args[0] $args[1..($args.Count - 1)]; if ($LASTEXITCODE) { throw "$($args[0]) failed ($LASTEXITCODE)" } }

Push-Location $Work
try {
    # /MT everywhere (CMP0091 lets CMAKE_MSVC_RUNTIME_LIBRARY choose it in both projects).
    $common = @('-A', 'x64', '-DCMAKE_POLICY_DEFAULT_CMP0091=NEW', '-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded')

    Run git clone -q --depth 1 --branch $MBEDTLS --recurse-submodules --shallow-submodules https://github.com/Mbed-TLS/mbedtls.git
    # libdatachannel's DTLS asks Mbed TLS for DTLS-SRTP, which is off by default.
    Run python mbedtls/scripts/config.py -f mbedtls/include/mbedtls/mbedtls_config.h set MBEDTLS_SSL_DTLS_SRTP
    Run cmake -S mbedtls -B mbedtls/build @common -DENABLE_TESTING=OFF -DENABLE_PROGRAMS=OFF `
        -DUSE_SHARED_MBEDTLS_LIBRARY=OFF -DUSE_STATIC_MBEDTLS_LIBRARY=ON "-DCMAKE_INSTALL_PREFIX=$Work/mbedtls-install"
    Run cmake --build mbedtls/build --config Release
    Run cmake --install mbedtls/build --config Release

    # Static Mbed TLS: its x509 and crypto libraries go on the link line too, and bcrypt, its entropy source.
    $mbed = "$Work/mbedtls-install/lib"
    Run git clone -q --depth 1 --branch $LIBDATACHANNEL --recurse-submodules --shallow-submodules https://github.com/paullouisageneau/libdatachannel.git
    Run cmake -S libdatachannel -B libdatachannel/build @common `
        -DNO_MEDIA=ON -DNO_WEBSOCKET=ON -DNO_EXAMPLES=ON -DNO_TESTS=ON -DUSE_MBEDTLS=ON `
        "-DCMAKE_PREFIX_PATH=$Work/mbedtls-install" "-DMbedTLS_ROOT=$Work/mbedtls-install" `
        "-DCMAKE_CXX_STANDARD_LIBRARIES=$mbed/mbedx509.lib $mbed/mbedcrypto.lib bcrypt.lib ws2_32.lib iphlpapi.lib"
    Run cmake --build libdatachannel/build --config Release

    Copy-Item libdatachannel/build/Release/datachannel.dll "$Out\datachannel.dll"
    Copy-Item libdatachannel/LICENSE "$Out\licenses\libdatachannel.txt"
    Copy-Item mbedtls/LICENSE "$Out\licenses\mbedtls.txt"
    Copy-Item libdatachannel/deps/libjuice/LICENSE "$Out\licenses\libjuice.txt"
    Copy-Item libdatachannel/deps/usrsctp/LICENSE.md "$Out\licenses\usrsctp.txt"
    # libopus, for the voice call (see build-linux.sh).
    Invoke-WebRequest "https://downloads.xiph.org/releases/opus/opus-$OPUS.tar.gz" -OutFile "opus-$OPUS.tar.gz"
    if ((Get-FileHash "opus-$OPUS.tar.gz" -Algorithm SHA256).Hash -ne $OPUS_SHA256) { throw "opus-$OPUS.tar.gz: wrong checksum" }
    Run tar -xzf "opus-$OPUS.tar.gz"
    Run cmake -S "opus-$OPUS" -B opus-build @common -DOPUS_BUILD_SHARED_LIBRARY=ON -DOPUS_BUILD_PROGRAMS=OFF `
        -DOPUS_BUILD_TESTING=OFF -DOPUS_INSTALL_PKG_CONFIG_MODULE=OFF
    Run cmake --build opus-build --config Release
    Copy-Item opus-build/Release/opus.dll "$Out\opus.dll"
    Copy-Item "opus-$OPUS/COPYING" "$Out\licenses\opus.txt"

    "libdatachannel $LIBDATACHANNEL, Mbed TLS $MBEDTLS, Opus $OPUS, MSVC x64, static C runtime" | Set-Content "$Out\VERSION"
} finally {
    Pop-Location
    Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue
}
Get-ChildItem $Out | Format-Table Name, Length
