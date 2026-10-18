# Snap Store listing

The pictures for Subtitld's listing in the Snap Store, uploaded on the
snap's **Listing** page at snapcraft.io, not packaged in the snap.
The desktop's icon, in the snap, is `snap/gui/icon.png`.

| File | Listing field | Size | The store takes |
|---|---|---|---|
| `icon.png` | Snap icon | 512 × 512 | 1:1, 40 to 512 px, 256 KB |
| `banner.png` | Featured banner | 3840 × 1280 | 3:1, 720 × 240 to 4320 × 1440, 2 MB |

The banner is the Microsoft Store listing's hero art, cut to 3:1 around
the band where the mark, the waveform and the subtitles run. Only a snap
with a banner can be first in the featured spot.

The screenshots are the Microsoft Store listing's, in
`../../msix/store-listing/screenshots`. The store takes five, of 480 × 480
to 3840 × 2160 and 2 MB at most each. Most of those PNGs are larger than
that; saved as JPEG (quality 92) at their size they come to about 1.5 MB.

## Making them again

`python packaging/branding/render-icons.py`, with the rest of the icons,
the hero art included.
