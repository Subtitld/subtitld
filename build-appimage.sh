#!/bin/bash
set -e

VERSION=${VERSION:-$(date -u +%y.%m.%d.%H%M)}
APP_DIR="Subtitld.AppDir"

# Install dependencies and build with PyInstaller
pip install .
pip install pyinstaller
pyinstaller subtitld.spec --clean

# Create AppDir structure
mkdir -p $APP_DIR/usr/{bin,share/applications,share/icons/hicolor/256x256/apps}

# Copy PyInstaller output
cp -r dist/subtitld $APP_DIR/usr/bin/

# Create wrapper script
cat > $APP_DIR/usr/bin/subtitld-wrapper << 'EOF'
#!/bin/bash
APPDIR="$(dirname "$(readlink -f "$0")")/.."
exec "$APPDIR/usr/bin/subtitld/subtitld" "$@"
EOF
chmod +x $APP_DIR/usr/bin/subtitld-wrapper

# Copy desktop file, icon and file types (for AppImage integration tools)
cp snap/gui/subtitld.desktop $APP_DIR/usr/share/applications/
cp snap/gui/subtitld.desktop $APP_DIR/
cp snap/gui/icon.png $APP_DIR/usr/share/icons/hicolor/256x256/apps/subtitld.png
cp snap/gui/icon.png $APP_DIR/subtitld.png
sed -i 's|Icon=.*|Icon=subtitld|' $APP_DIR/usr/share/applications/subtitld.desktop
sed -i 's|Icon=.*|Icon=subtitld|' $APP_DIR/subtitld.desktop
mkdir -p $APP_DIR/usr/share/mime/packages
cp flatpak/org.subtitld.Subtitld.mime.xml $APP_DIR/usr/share/mime/packages/org.subtitld.Subtitld.xml
cp -r flatpak/icons/hicolor $APP_DIR/usr/share/icons/

# Create AppRun
# QT_XCB_GL_INTEGRATION=none avoids bundled libGLX/libstdc++ clashing
# with host drivers (GLX FBConfig fails on some systems otherwise).
# QtMultimedia renders video via its own bundled ffmpeg, so disabling
# Qt's GLX integration has no visible effect on playback.
cat > $APP_DIR/AppRun << 'EOF'
#!/bin/bash
APPDIR="$(dirname "$(readlink -f "$0")")"
export QT_XCB_GL_INTEGRATION=${QT_XCB_GL_INTEGRATION:-none}
exec "$APPDIR/usr/bin/subtitld/subtitld" "$@"
EOF
chmod +x $APP_DIR/AppRun

# appimagetool and the runtime it puts in front of the AppImage, pinned. The
# runtime is static: the AppImage runs without libfuse2, which Ubuntu (since
# 22.04) and other current distributions no longer install. AppImageKit's
# appimagetool, used before, made AppImages that need it.
wget -q -O appimagetool-x86_64.AppImage \
    https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage
wget -q -O runtime-x86_64 \
    https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64
sha256sum -c - <<'SUMS'
ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0  appimagetool-x86_64.AppImage
2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d  runtime-x86_64
SUMS
chmod +x appimagetool-x86_64.AppImage

# Build AppImage
ARCH=x86_64 ./appimagetool-x86_64.AppImage --appimage-extract-and-run --runtime-file runtime-x86_64 \
    $APP_DIR Subtitld-$VERSION-x86_64.AppImage
