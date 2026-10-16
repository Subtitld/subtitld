# Microsoft Store listing

The pictures for Subtitld's listing, uploaded in Partner Center
(the submission's **Store listings** step), not packaged in the MSIX.
The package's own tiles are in `../assets`.

| File | Partner Center field | Size |
|---|---|---|
| `screenshots/*.png` | Screenshots, Desktop | 3840 × 2160 |
| `tile-icon.png` | Store logos, 1:1 App tile icon | 300 × 300 |
| `hero.png` | Windows 10 or Windows 11 and Xbox image, 16:9 Super hero art | 3840 × 2160 |

The 2:3 poster art and 1:1 box art are for games, and are left out.

## Making them again

- `tile-icon.png` and `hero.png`: `python packaging/branding/render-icons.py`,
  with the rest of the icons. The hero has no text and no screenshot of the
  app, and nothing in its bottom third, as the Store asks.
- The screenshots: `store.py` in the showcase's capture harness, in English:

  ```
  OUT=dir LANG=en_US.UTF-8 LANGUAGE=en python harness.py store.py "project/Tears of Steel.usfx"
  ```

  Run it with another `LANG` for a listing in another language.

## Captions

Partner Center takes a caption of up to 200 characters for each screenshot.

1. **editor**: Edit subtitles on the video and a timeline with its waveform, each speaker in a colour, each line's dub below it.
2. **speakers**: Name the speakers, give each a colour and a voice, and see each on a track of its own.
3. **translations**: Keep translations beside the original, in the list and on the timeline.
4. **audio-effects**: Shape the voice and the music apart: equalizer, compressor and gate, track by track.
5. **export**: Export subtitles in dozens of formats, the translation or both, and audio and video with the dub.
6. **vertical-timeline**: A vertical timeline beside the video, for timing line by line.
