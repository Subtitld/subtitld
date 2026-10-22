# HTML subtitle overlay spike

Throwaway test: a transparent `QWebEngineView` over the same
`QMediaPlayer` + `QGraphicsView` + `QGraphicsVideoItem` setup the preview panel uses.

    spikes/html_overlay/make_test_video.sh
    .venv/bin/python spikes/html_overlay/spike.py --autoplay --lead 80

- The host pushes the start time of every frame the video sink shows to the page
  (`sync()`); the page draws cues on a 1920x1080 stage scaled to the video rect.
- CSS animations are paused and scrubbed to media time, so pause, seek and
  frame step move them with the video.
- The page draws its own frame number (pink) under the one burned into the
  video (black). They should match.

Keys: space play/pause · ←/→ seek 1 s · ,/. step one frame · 1/2/3 rate 0.5/1/2 ·
m frame/extrapolated clock · [/] lead -/+10 ms · q quit

## Offscreen rendering (`render_bench.py`)

Renders frames at fixed media times in hidden views and reads the binary frame
code back from each image to catch captures of the wrong frame. Results on the
dev machine (Wayland, 25 fps test cues, 250 frames, all animated):

| capture | views | size | throughput | wrong frames |
|---|---|---|---|---|
| `grab()`, wait 2 rAF | 1 | 960x540 | 30 fps (1.2x real time) | 4/250 |
| `grab()`, wait 3 rAF | 1 | 960x540 | 20 fps (0.8x) | 0 |
| `grab()`, wait 3 rAF | 4 | 960x540 | 79 fps (3.2x) | 0 |
| `grab()`, wait 3 rAF | 8 | 960x540 | 154 fps (6.2x) | 3, first frames only |
| `grab()`, wait 3 rAF | 4 | 1920x1080 | 75 fps (3.0x) | 0 |
| `grab()`, wait 3 rAF | 8 | 1920x1080 | 74 fps (2.9x) | 4, first frames only |
| DevTools `captureScreenshot` PNG | 1 | 1920x1080 | ~10.6 fps per view | 0 |
| DevTools `captureScreenshot` WebP | 1 | 1920x1080 | ~3 fps per view | 0 |

A single view is paced by Chromium's ~33 Hz frame clock, not by drawing, so
parallel views scale until the machine saturates (around 75 fps at 1080p).

    .venv/bin/python spikes/html_overlay/render_bench.py --views 4 --rafs 3 --size 960x540 --frames 250 --no-analysis

## Cached playback (`cached_player.py`)

4 hidden views render overlay frames up to 4 s ahead of the playhead; the page
reports its content rects, so each frame is kept as a few cropped images. The
video item's `paint()` draws the cached crops for the frame it is painting, so
no lead is needed. Each capture is checked against its frame code and rendered
again if wrong. A frame not cached yet draws nothing and counts as a miss.

    .venv/bin/python spikes/html_overlay/cached_player.py --autoplay

Keys as in `spike.py`, plus `e` edit the cue under the playhead, `c` clear the cache.

On the dev machine: 38/38 snapshots during playback show the same frame number
in the video and in the overlay; 1 miss at start, 3 misses (120 ms) after
editing the cue under the playhead; renderers hold 4 s ahead at 25 renders/s.

Take the painted frame's time from `videoFrameChanged`, not from
`videoSink().videoFrame()`: the sink is updated from the decoder thread and can
be a frame ahead of what the item paints.
