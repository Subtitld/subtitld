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

## Live fallback (in `cached_player.py`)

The live overlay from `spike.py` keeps running (with `--lead`) and covers frames
the cache does not have yet:

- The source is chosen when a frame arrives and applied by moving the live view
  into the video rect or parking it outside. Parking, not hiding, keeps
  Chromium drawing, so the view is current the moment it comes back; Qt applies
  the move in the same repaint as the frame.
- The item can paint a frame before `videoFrameChanged` reaches us, so while
  playing the choice covers the next frame too: the live view comes in if this
  frame or the next is not cached. When it is in, the cache draws nothing, so
  the two never overlap.
- Seeks decide for the target frame at once, move the live page there and
  point the renderers at it, without waiting for the decoder's first frame.
- The live page reports the frame it has drawn (3 animation frames later, as
  with offscreen captures). Until that is near the frame on screen, nothing is
  drawn: right after a seek the live view would otherwise show the old
  position.

Seeking 3 s every 3 s with an empty cache: cache frames exact, live frames
within 2 frames of the video, no stale or doubled content in snapshots, about
5 blank frames (200 ms) after each seek while the live view catches up.

    .venv/bin/python spikes/html_overlay/cached_player.py --autoplay --no-preroll --seek-every 3

`--no-live` turns the fallback off; `--seek-by -1000` seeks backwards. The
stats line also counts the source per decoded frame (`decoded:`), which
holds when the window is not being repainted (covered or on another
workspace); the per-paint counts do not.

## Disk cache (`disk_cache.py`)

The cache unit is a cue layer: one cue at one time inside it, rendered alone
(`renderAt(t, id, rafs, cue)`), keyed by `(cue key, offset ms)`. The cue key
hashes everything that changes its pixels: page version (hash of
`overlay.html`), render size, style, text, duration, sub-frame phase, and the
test ruler. An edit is a new key; undo finds the old one. Frames without cues
need no render; overlapping cues composite layer by layer.

Layers live in RAM around the playhead and in SQLite on disk (default
`~/.cache/subtitld-spike/overlay.sqlite`): a `layers` table (key, offset, crop
rect, image hash, last used) and a `blobs` table storing each distinct image
once, so a cue's static hold is one image. Images are raw premultiplied ARGB32
(what Qt draws from) compressed with zstd level 1 (standard library from Python
3.14, else the `zstandard` package), falling back to zlib level 1; each image
records its codec. Older cache layouts are dropped (`PRAGMA user_version`). One writer thread encodes and
writes, two reader threads load and decode; a queued signal hands results to
the GUI thread. Every 5 s, reads are recorded and the cache is trimmed to
`--disk-limit` MB, least recently used first. Renderers draw the frame stamp
under the stage (`?stamp=below`) so captures are still checked without the
stamp being stored; the test HUD is hidden (`?hud=0`) since its per-frame
counter would make every frame unique.

On the dev machine, 540p, the 30 s test video:

- cold: 750 layers rendered, 5.6 MB on disk (6.9 MB as PNG), 278 distinct images
- warm: 0 renders, 750 layers from disk, load p50 0.2 ms

Image formats on those 278 images (~125k px each, 134 MB raw; one thread):

| format | size | encode | decode |
|---|---|---|---|
| Qt PNG q70 (first version) | 7.1 MB | 3.7 ms | 1.0 ms |
| Qt PNG max compression | 5.9 MB | 36 ms | 1.0 ms |
| Qt WebP lossless | 3.6 MB | 20 ms | 0.45 ms |
| JPEG XL lossless, effort 1 | 5.9 MB | 1.8 ms | 2.6 ms |
| AVIF "lossless" (not exact) | 9.4 MB | 20 ms | 10.5 ms |
| QOI | 10.1 MB | 0.23 ms | 0.13 ms |
| raw + LZ4 | 8.0 MB | 0.20 ms | 0.14 ms |
| **raw + zstd 1** | 5.8 MB | 0.30 ms | 0.19 ms |
| raw + zlib 1 (fallback) | 7.0 MB | 1.3 ms | 0.47 ms |

Seeking every 1.5 s with an edit at 5 s and its undo at 9 s:

| | RAM only | with disk cache (warm) |
|---|---|---|
| seek back 3 s | 48 live + 33 blank frames | 3 live + 8 blank |
| seek forward 3 s | 41 live + 26 blank | 6 live + 2 blank |

Only the edited cue renders; the undo loads from disk.

    .venv/bin/python spikes/html_overlay/cached_player.py --autoplay            # warm after one run
    .venv/bin/python spikes/html_overlay/cached_player.py --autoplay --clear-disk

## CPU and memory (`measure.py`)

Whole process tree (app + QtWebEngineProcess children), steady playback of the
25 fps test video after load and preroll. CPU is % of one core; memory is PSS.
i7-13700T, Intel UHD 770, Wayland. QtWebEngine runs its GPU work in the main
process, so "main" includes Chromium's compositing and the grabs.

| scenario | CPU | PSS | vs. baseline |
|---|---|---|---|
| player only, no overlay | 21% | 272 MB | – |
| live overlay (`spike.py`) | 38% | 480 MB | +17%, +208 MB |
| cached, 4 views 540p, paused, cache full | 5% | 484 MB | idle |
| cached, 1 view 540p, playing (can't keep up) | 35% | 504 MB | +14%, +232 MB |
| cached, 2 views 540p, playing | 46% | 612 MB | +25%, +340 MB |
| cached, 4 views 540p, playing | 50% | 646 MB | +29%, +374 MB |
| cached, 8 views 540p, playing | 55% | 717 MB | +34%, +445 MB |
| cached, 4 views 1080p, playing | 61% | 1048 MB | +40%, +776 MB |

    .venv/bin/python spikes/html_overlay/measure.py --warmup 8 --duration 14 -- \
        .venv/bin/python spikes/html_overlay/cached_player.py --autoplay --views 4
