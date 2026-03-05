#!/bin/bash
set -e

VERSION=$(date +%y.%m.%d.%H%M)
APP_DIR="Subtitld.AppDir"

# Create AppDir structure
mkdir -p $APP_DIR/usr/{bin,lib,share/applications,share/icons/hicolor/256x256/apps}

# Install Python dependencies
pip install --target=$APP_DIR/usr/lib .

# Copy executable
cat > $APP_DIR/usr/bin/subtitld << 'EOF'
#!/bin/bash
APPDIR="$(dirname "$(readlink -f "$0")")/.."
export PYTHONPATH="$APPDIR/usr/lib:$PYTHONPATH"
export LD_LIBRARY_PATH="$APPDIR/usr/lib:$LD_LIBRARY_PATH"
exec python3 -m subtitld "$@"
EOF
chmod +x $APP_DIR/usr/bin/subtitld

# Copy desktop file and icon
cp snap/gui/subtitld.desktop $APP_DIR/usr/share/applications/
cp snap/gui/subtitld.desktop $APP_DIR/
cp snap/gui/icon.png $APP_DIR/usr/share/icons/hicolor/256x256/apps/subtitld.png
cp snap/gui/icon.png $APP_DIR/subtitld.png
sed -i 's|Icon=.*|Icon=subtitld|' $APP_DIR/usr/share/applications/subtitld.desktop
sed -i 's|Icon=.*|Icon=subtitld|' $APP_DIR/subtitld.desktop
sed -i '/^Encoding=/d' $APP_DIR/subtitld.desktop
sed -i 's/^Info=/X-Info=/' $APP_DIR/subtitld.desktop
sed -i 's/Categories=Application;Multimedia/Categories=AudioVideo;/' $APP_DIR/subtitld.desktop

# Create AppRun
cat > $APP_DIR/AppRun << 'EOF'
#!/bin/bash
APPDIR="$(dirname "$(readlink -f "$0")")"
export PATH="$APPDIR/usr/bin:$PATH"
export LD_LIBRARY_PATH="$APPDIR/usr/lib:$LD_LIBRARY_PATH"
export PYTHONPATH="$APPDIR/usr/lib:$PYTHONPATH"
exec "$APPDIR/usr/bin/subtitld" "$@"
EOF
chmod +x $APP_DIR/AppRun

# Download appimagetool
wget -q https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage
chmod +x appimagetool-x86_64.AppImage

# Build AppImage
ARCH=x86_64 ./appimagetool-x86_64.AppImage --appimage-extract-and-run $APP_DIR Subtitld-$VERSION-x86_64.AppImage
