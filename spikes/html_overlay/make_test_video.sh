#!/bin/sh
# A 30 s, 25 fps test video with its frame number and time burned in, so the
# HTML overlay's own frame counter can be compared against the picture.
set -e
out="${1:-$(dirname "$0")/test_25fps.mp4}"
ffmpeg -y -loglevel error \
    -f lavfi -i testsrc2=size=1920x1080:rate=25 -t 30 \
    -vf "drawbox=x=30:y=30:w=760:h=100:color=black@0.7:t=fill,\
drawtext=font=DejaVu Sans Mono:fontsize=64:fontcolor=white:x=50:y=48:text='VIDEO %{eif\:n\:d\:5}  %{pts\:hms}'" \
    -c:v libx264 -pix_fmt yuv420p -g 25 -preset veryfast "$out"
echo "$out"
