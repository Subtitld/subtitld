# Making a Subtitld timeline theme

A timeline theme is how Subtitld's timeline looks: its background, ruler,
grid, waveform and playhead, and the boxes its subtitles, titles and marks
are drawn as. It is one JSON file that carries its pictures in it, as SVG.
This document is the whole reference: a person or an AI should be able to
write a working theme from it alone.

- [The file](#the-file)
- [Colours](#colours)
- [The timeline's settings](#the-timelines-settings)
- [Boxes: subtitles, titles and marks](#boxes-subtitles-titles-and-marks)
- [Shapes: SVG in a theme](#shapes-svg-in-a-theme)
- [Frames: a box drawn by an SVG](#frames-a-box-drawn-by-an-svg)
- [Options: what the theme's user can change](#options-what-the-themes-user-can-change)
- [Dark and light](#dark-and-light)
- [What a theme may not contain](#what-a-theme-may-not-contain)
- [Trying a theme](#trying-a-theme)
- [Starter file](#starter-file)
- [Checklist before sharing](#checklist-before-sharing)

## The file

A theme is a single `.json` file, UTF-8, at most 4 MB:

```json
{
  "format": 1,
  "id": "yourname.slate",
  "name": "Slate",
  "author": "Your Name",
  "version": "1.0",
  "description": "Grey boxes with a bright playhead.",
  "look": "dark",
  "timeline": {"cursor_color": "#ff4fc3f7"},
  "boxes": {"subtitle": {"fill_color": "#e6607080", "frame_svg": "box"}},
  "shapes": {"box": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 100 40\">…</svg>"},
  "options": []
}
```

| Key | Required | What it is |
|---|---|---|
| `format` | yes | Always `1`. |
| `id` | yes | Lowercase letters, digits, `.`, `_` and `-`, starting with a letter or digit. Use `yourname.themename`. The ids `subtitld.*` belong to Subtitld's own themes. |
| `name` | yes | What people see. Not empty. |
| `author`, `version`, `description` | no | Text. |
| `look` | no | `"dark"` or `"light"`: the interface look the theme is drawn for. See [Dark and light](#dark-and-light). |
| `counterpart` | no | The `id` of the same theme for the other look. Only with `look`. |
| `timeline` | no | The timeline's own settings. See [The timeline's settings](#the-timelines-settings). |
| `boxes` | no | One object of settings per kind of cue: `subtitle`, `title`, `mark`. See [Boxes](#boxes-subtitles-titles-and-marks). |
| `shapes` | no | SVG pictures by a key of your choosing, at most 64. Settings that take a picture name one of these keys. |
| `options` | no | What the theme's user may change, at most 32. See [Options](#options-what-the-themes-user-can-change). |

Every setting a theme leaves out is Subtitld's default (the tables below
give each one). A setting Subtitld does not know is ignored, and so is a
value its setting cannot hold (a colour that is not a colour, a number out
of range is brought into it): the theme still loads. What makes Subtitld
refuse a theme whole is listed in
[What a theme may not contain](#what-a-theme-may-not-contain).

Coordinates are pixels of the timeline as it lies on its side: x runs
along time, left to right; y from the timeline's top down. The timeline's
height is the user's to change, so place things from its top (`top`,
`ruler_offset`, `waveform_top`) or its bottom (`bottom`, `waveform_bottom`).
The top 25 px or so lie under the player's controls bar: start what must
be seen below that. Subtitld also has a vertical timeline, which draws
the same theme turned on its side (mirrored across the diagonal): time runs
down, and the timeline's top is its left. Nothing in the theme needs to
change for it.

## Colours

A colour is `#AARRGGBB`: alpha **first**, then red, green and blue, in hex.
`#ff000000` is opaque black, `#80ffffff` half-transparent white,
`#00000000` nothing at all. `#RRGGBB` is read as opaque.

## The timeline's settings

These go in `"timeline"`.

### Background

| Setting | Type | Default | |
|---|---|---|---|
| `background_color` | colour | `#00000000` | Behind everything. Transparent shows the window's own ground. |
| `ruler_background_color` | colour | `#00000000` | Behind the ruler's band. |
| `background_svg` | shape | | A tile repeated along the timeline: its own width, stretched to the timeline's height, scrolling with time. `currentColor` in it is `background_color`. |
| `ruler_background_svg` | shape | | The same for the ruler's band, in `ruler_background_color`. |

### Ruler

| Setting | Type | Default | |
|---|---|---|---|
| `show_ruler` | bool | `true` | The time labels. |
| `time_text_color` | colour | `#b8b8cee0` | |
| `ruler_font` | font | `Ubuntu Mono` | A font family name. One the user lacks falls back to another. |
| `ruler_font_size` | int, pt | `8` | 5–24 |
| `ruler_time_format` | select | `clock` | `clock` (mm:ss), `seconds`, `timecode` (hh:mm:ss:ff), `frames` |
| `ruler_position` | select | `top` | `top` or `bottom` of the timeline |
| `ruler_offset` | int, px | `27` | 0–200, from that edge |
| `ruler_label_spacing` | int, px | `75` | 30–400: the least room between two labels |
| `ruler_ticks` | bool | `false` | A tick at each label, shorter ones between |
| `ruler_tick_color` | colour | `#99b8cee0` | |
| `ruler_tick_length` | int, px | `8` | 2–60 |
| `ruler_subdivisions` | int | `4` | 0–10 short ticks between two labels |
| `ruler_tick_svg`, `ruler_subtick_svg` | shape | | A tick as an SVG: as long as the tick (half for a short one), as wide as its shape's proportions make it, centred on its time, pointing down from the ruler (drawn for a ruler at the top). |

### Grid

| Setting | Type | Default | |
|---|---|---|---|
| `grid_color` | colour | `#336a7483` | A line at each label's time |
| `grid_width` | number, px | `1.0` | 0.5–6 |
| `grid_line_svg` | shape | | The line as an SVG: its own width, the timeline's height, centred on its time. |

### Waveform

| Setting | Type | Default | |
|---|---|---|---|
| `show_waveform` | bool | `true` | |
| `waveform_fill_color` | colour | `#cc153450` | |
| `waveform_border_color` | colour | `#ff153450` | |
| `waveform_border_width` | number, px | `1.0` | 0–6 |
| `waveform_top` | int, px | `45` | 0–400, from the timeline's top |
| `waveform_bottom` | int, px | `20` | 0–400, from its bottom |
| `waveform_scale` | int, % | `90` | 10–300: how tall the loudest sound is drawn |

### Playhead (cursor)

| Setting | Type | Default | |
|---|---|---|---|
| `cursor_color` | colour | `#ccff0000` | |
| `cursor_width` | number, px | `2.0` | 0.5–8 |
| `cursor_line_style` | select | `solid` | `solid`, `dashed`, `dotted`, `custom` |
| `cursor_line_svg` | shape | | With `custom`: stretched to the line's width and the timeline's height. |
| `cursor_glow` | int, px | `0` | 0–20: a fainter, wider line behind it |
| `cursor_head` | select | `none` | `none`, `triangle`, `circle`, `flag`, `custom`: a shape where the line crosses the ruler's edge |
| `cursor_head_svg` | shape | | With `custom`: as tall as `cursor_head_size`, as wide as its proportions, the line through its middle. |
| `cursor_head_size` | int, px | `10` | 4–30 |
| `cursor_show_time` | bool | `false` | The playhead's time beside it |
| `cursor_time_background_color` | colour | `#ccff0000` | |
| `cursor_time_text_color` | colour | `#ffffffff` | |
| `cursor_time_svg` | shape | | A [frame](#frames-a-box-drawn-by-an-svg) behind the time, in its background colour. |

The badge that says playback repeats or is sped up:

| Setting | Type | Default | |
|---|---|---|---|
| `cursor_badge_shape` | select | `slanted` | `slanted`, `rectangle`, `rounded`, `custom` |
| `cursor_badge_svg` | shape | | With `custom`: a [frame](#frames-a-box-drawn-by-an-svg), in the badge's colour. |
| `cursor_badge_side` | select | `left` | `left` or `right` of the line |
| `cursor_badge_top` | int, px | `25` | 0–400 |
| `cursor_badge_color` | colour | `#96ff0000` | |
| `cursor_text_indicator` | colour | `#ffffffff` | The badge's text |

### Marks and helpers

| Setting | Type | Default | |
|---|---|---|---|
| `selected_subtitle_arrow_color` | colour | `#ff969696` | The arrows by the selected cue, and the push handle |
| `dub_waveform_color` | colour | `#ffffffff` | A dubbed cue's waveform |
| `onset_marker_color` | colour | `#ffffd9a0` | Where speech starts |
| `repeat_area_color` | colour | `#50ff0000` | The stretch repeat playback plays |
| `repeat_area_gradient` | bool | `true` | Fading downwards, or even |
| `offscreen_arrow_color` | colour | `#dcff0000` | Where the playhead is, when out of view |
| `offscreen_arrow_svg` | shape | | Its own size, pointing **right** (mirrored for the left side). |
| `onset_marker_svg` | shape | | Its own width, the timeline's height, its left at its time. |
| `push_handle_svg` | shape | | Its own width, the subtitles' lane's height, centred on its time. |

## Boxes: subtitles, titles and marks

`"boxes"` holds one object per kind of cue:

- `subtitle`: the subtitles.
- `title`: titles, in a band of their own above the subtitles (pushing
  the subtitles' lane down while it shows).
- `mark`: marks (chapters, notes), a thin strip under the ruler. A mark's
  fill is its category's colour; the theme's `fill_color` gives only its
  opacity.

A kind takes the subtitles' settings for what it does not set, except
where it sits and its frames: titles and marks have places and plain
boxes of their own until the theme sets them (a subtitle frame drawn for
a tall box would not suit a 12 px strip). A kind Subtitld does not know
yet is kept as written, for the version that will.

### Where the boxes are

| Setting | Type | Default | |
|---|---|---|---|
| `full_height` | bool | `false` | From the timeline's top to its bottom |
| `top` | int, px | `55` | 0–400, from the timeline's top |
| `bottom` | int, px | `25` | 0–400, from its bottom |
| `height` | int, px | `0` | 0–400: a band this tall from `top`, whatever the timeline's height (0: down to `bottom`) |
| `gap` | int, px | `0` | 0–20: space between two boxes that touch |

Titles default to `top` 55, `height` 16; marks to `top` 41, `height` 12.

### The box as Subtitld draws it

Used when the box has no frame (`frame_svg`).

| Setting | Type | Default | |
|---|---|---|---|
| `corner_radius` | int, px | `3` | 0–40 |
| `border_width` | number, px | `0.0` | 0–6 |
| `border_color` | colour | `#ff6a7483` | |
| `selected_border_color` | colour | `#ffb8cee0` | |
| `fill_color` | colour | `#e6c8dbe9` | Also a frame's `currentColor` |
| `selected_fill_color` | colour | `#cc3e5363` | |
| `fill_gradient` | bool | `false` | From the fill colour at the top to the colours below at the bottom |
| `fill_gradient_color` | colour | `#ff8fa9bc` | |
| `selected_fill_gradient_color` | colour | `#cc1f2a33` | |
| `speaker_strip_height` | int, px | `3` | 1–20: the speaker's colour along the top. Left off when a frame draws `speakerColor`. |

### Frames

| Setting | Type | |
|---|---|---|
| `frame_svg` | shape | The box as an SVG [frame](#frames-a-box-drawn-by-an-svg), in place of the fill and border |
| `frame_selected_svg`, `frame_hover_svg`, `frame_locked_svg`, `frame_failed_svg` | shape | One per state; `frame_svg` for any not set. *Failed* is a cue that fails the quality check. |
| `lock_svg` | shape | The padlock on a locked box: its own size, at the box's top right, in the selected text colour |

### Text

Subtitld draws the cue's text itself, over the box or frame.

| Setting | Type | Default | |
|---|---|---|---|
| `text_color` | colour | `#ff304251` | |
| `selected_text_color` | colour | `#ffb8cee0` | |
| `failed_check_text_color` | colour | `#ff9e1a1a` | |
| `font` | font | `Montserrat` | |
| `font_size` | int, pt | `10` | 5–32 |
| `font_bold` | bool | `false` | |
| `text_vertical_alignment` | select | `top` | `top`, `center`, `bottom` |
| `padding_x` | int, px | `26` | 0–100 |
| `padding_y` | int, px | `6` | 0–100 |

### Edges

The start and end of a box are dragged to retime the cue.

| Setting | Type | Default | |
|---|---|---|---|
| `edge_style` | select | `glow` | `glow` (fading inwards), `bar` (a line along it), `none` |
| `edge_show` | select | `hover` | `hover` (the edge under the mouse) or `always` (every box's both edges) |
| `edge_handle_width` | int, px | `20` | 2–80: how far a glow reaches in |
| `edge_handle_thickness` | number, px | `2.0` | 0.5–12 |
| `edge_start_color`, `edge_end_color` | colour | `#ffffffff` | |
| `end_slant` | int, ° | `90` | 45–90: the angle of the end side as your frame draws it, so the end's bar runs along it (90 is upright) |
| `start_line` | select | `box` | `none`, `box` (a line inside the box), `across` (the line across the timeline too, fading by the middle) |
| `edge_grab_width` | int, px | `20` | 4–80: how near an edge the mouse grabs it, whatever is drawn |

## Shapes: SVG in a theme

A setting of type *shape* holds a key of `"shapes"`; `"shapes"` holds the
SVG itself as a JSON string (escape its `"` as `\"`, or use `'` in the
SVG's attributes). One shape may serve several settings. A setting that
names a key the theme does not carry makes the theme refused.

```json
"shapes": {
  "tick": "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 2 10'><rect width='2' height='10' fill='currentColor'/></svg>"
},
"timeline": {"ruler_ticks": true, "ruler_tick_svg": "tick"}
```

- **Size.** One SVG unit is one pixel. A shape's "own size" is its
  `viewBox` (or its `width` and `height`). Where a setting gives it a
  height, its width keeps its proportions; where it stretches, it
  stretches both ways (see each setting).
- **`currentColor`** is the colour of what the shape draws: the setting's
  colour next to it (the cursor's colour for the cursor's shapes, and so
  on). The colour's alpha is the shape's opacity. A shape that never says
  `currentColor` is drawn in its own colours, fully opaque.
- Drawn once per size and colour, then copied: a shape costs no more
  while the video plays than Subtitld's own drawing.
- Qt draws them (SVG Tiny 1.2 and most of SVG 1.1): paths, shapes,
  gradients, `<use>`, `<style>` in the file. No filters, masks are partly
  supported; text in a shape is drawn in whatever font the machine has.

## Frames: a box drawn by an SVG

A frame is an SVG drawn as a box of any width and height. Each element at
the SVG's top level (a `<g>`, a `<rect>`, a `<path>`…) is a **part**, laid
out on its own by its `data-` attributes; `<defs>`, `<style>`, `<title>`,
`<desc>` and `<metadata>` are not parts. The `viewBox` is the frame's
design size, at one unit a pixel.

| Attribute | Values | |
|---|---|---|
| `data-x` | `left`, `right`, `center` | Keeps its width, stays at that side or the middle |
| | `stretch` (default) | Keeps its distances to both sides: grows with the box |
| | `scale` | Grows and moves in proportion |
| `data-y` | `top`, `bottom`, `center`, `stretch` (default), `scale` | The same, up and down |
| `data-slice` | `8`, `4 8`, `2 8 2 8` | Insets kept unstretched (one for all sides; top-bottom and left-right; or top, right, bottom, left, as CSS writes them): corners stay round as the part grows |
| `data-hit` | `start`, `end`, `body` | Where the mouse grabs the start, the end, or the whole cue to move it. The part's rectangle, stroke included. A frame with any `data-hit` part ignores clicks on the rest of the box; one without leaves grabbing to `edge_grab_width`. |

A part with no attributes stretches both ways: the whole box. A part that
stretches without `data-slice` stretches its corners too.

Colours in a frame:

- `currentColor` is the box's fill for its state (`fill_color`, or
  `selected_fill_color` when selected), and the part is drawn as
  transparent as that colour is.
- `speakerColor` is the colour of the cue's speaker (the fill when the
  project has no speaker colours). When any of a kind's frames uses it,
  Subtitld leaves its own speaker strip off all of that kind's boxes: the
  frame places the speaker's colour.

Classic's subtitle box, a rounded box in its fill with the speaker's
colour along the top:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 40">
  <g data-slice="3"><rect width="100" height="40" rx="3" fill="currentColor"/></g>
  <g data-y="top" data-slice="3 3 0 3"><path d="M0 3 A3 3 0 0 1 3 0 H97 A3 3 0 0 1 100 3 Z" fill="speakerColor"/></g>
</svg>
```

A box with grips you can see, the middle moving the cue:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 40">
  <g data-slice="4"><rect width="100" height="40" rx="4" fill="currentColor"/></g>
  <g data-x="left" data-hit="start"><rect width="6" height="40" rx="3" fill="speakerColor"/></g>
  <g data-x="right" data-hit="end"><rect x="94" width="6" height="40" rx="3" fill="speakerColor"/></g>
  <g data-hit="body"><rect x="6" width="88" height="40" fill="none"/></g>
</svg>
```

Boxes can be narrower than your design: a box a few pixels wide still
draws its `left` and `right` parts at their own width. Keep end parts
slim.

## Options: what the theme's user can change

In Subtitld, a theme's card in Interface ▸ Timeline has an options
button that opens the theme's options. Each option is a field, and says which of the
theme's settings it sets:

```json
"options": [
  {"key": "box_color", "type": "color", "label": "Subtitle color", "default": "#ff607080",
   "sets": [{"box": "subtitle", "key": "fill_color", "part": "color"}]},
  {"key": "box_opacity", "type": "int", "label": "Subtitle opacity", "unit": "%", "default": 90,
   "sets": [{"box": "subtitle", "key": "fill_color", "part": "alpha"}]},
  {"key": "playhead", "type": "color", "label": "Cursor color", "default": "#ff4fc3f7",
   "sets": [{"key": "cursor_color", "part": "color"}, {"key": "cursor_badge_color", "part": "color"}]}
]
```

| Key | |
|---|---|
| `key` | Its name, unique in the theme |
| `type` | `color`, `int`, `number`, `bool`, `select`, `font` |
| `default` | Its value before the user changes it. Make it what the theme's settings already are. |
| `label` | What the user reads |
| `min`, `max`, `step`, `unit` | For numbers. Left out, `min` and `max` are those of what it sets (0–100 for an opacity). |
| `options` | For `select`: the list of values, each a value or `{"value": …, "label": …}` |
| `sets` | One or more settings: `key`, with `box` (`subtitle`, `title`, `mark`) for a box's, without it for the timeline's; and `part` |

`part`:

- left out: the option's value is the setting's value, as is (the
  setting must be able to hold the default);
- `"color"`: a colour option sets a colour setting's red, green and blue,
  keeping the theme's alpha;
- `"alpha"`: a number from 0 to 100 sets a colour setting's opacity, in
  percent.

Shapes cannot be options. What the user chose is kept per theme, and
survives a new version of the theme that still has the option.

## Dark and light

Subtitld's interface has a dark and a light look. A theme drawn for one
says so with `"look"`. To have it follow the interface, make two themes,
one per look, each naming the other in `"counterpart"`:

```json
{"id": "yourname.slate", "look": "dark", "counterpart": "yourname.slate-light", …}
{"id": "yourname.slate-light", "look": "light", "counterpart": "yourname.slate", …}
```

When the user switches the interface's look, a theme they have not changed
gives way to its counterpart. On subtitld.cc, upload both and link them as
a pair when you publish the second.

Colours that read on a dark ground (`#1a232b`, the panel behind a dark
timeline) rarely read on a light one (`#dde4eb`): draw both.

## What a theme may not contain

Subtitld refuses the whole theme when:

- it is not JSON, is over 4 MB, or `format` is not `1`;
- `id` or `name` is missing or not as above, or `look` is not `dark` or
  `light`, or `counterpart` is given without `look`;
- it has more than 64 shapes or 32 options;
- a setting names a shape it does not carry;
- an option is malformed, sets a setting there is not, sets a shape, or
  cannot hold its own default.

And refuses a shape (so the theme) that:

- is over 256 KB or has over 5000 elements;
- is not XML, or not an `<svg>`;
- has a DOCTYPE or entities;
- has `<script>`, `<foreignObject>`, `<image>`, `<iframe>`, `<audio>`,
  `<video>`, `<handler>` or `<listener>`;
- has an event attribute (`onclick`…);
- links outside itself: an `href` not starting with `#`, a `url()` not
  starting with `#`, `@import` in a `<style>`;
- is one Qt cannot draw.

A theme can therefore reach nothing outside its file: no pictures, fonts
or pages from elsewhere. Fonts are named, not carried: use common
families, and expect another where the user lacks one.

## Trying a theme

- **In Subtitld:** put the file in the `timeline_themes` folder of
  Subtitld's data folder, and start Subtitld. The theme shows among *Your
  themes* in Interface ▸ Timeline. The data folder is
  `~/.local/share/subtitld` on Linux, `%LOCALAPPDATA%\subtitld\subtitld`
  on Windows, `~/Library/Application Support/subtitld` on macOS. A theme
  Subtitld refuses is not listed: check it against
  [What a theme may not contain](#what-a-theme-may-not-contain).
- **On subtitld.cc:** *Upload a theme* checks the file with Subtitld
  itself and draws its preview (two speakers, touching cues, one selected,
  one locked, onset markers, the waveform and the playhead) before
  anything is published, and says why, if it refuses it. Discard it if
  you only wanted to look.

The theme's id must stay the same from one version to the next: that is
how Subtitld knows it is the same theme.

## Starter file

A complete dark theme: slate boxes with round corners, the speaker's
colour as a bar at the start, a cyan playhead with a head, ticks on the
ruler, and two options. Copy it, change the `id`, and work from there.

```json
{
  "format": 1,
  "id": "yourname.slate",
  "name": "Slate",
  "author": "Your Name",
  "version": "1.0",
  "description": "Slate boxes, the speaker's colour at their start, a cyan playhead.",
  "look": "dark",
  "timeline": {
    "background_color": "#ff151b21",
    "ruler_background_color": "#ff0f1418",
    "time_text_color": "#ff8a9aa8",
    "ruler_ticks": true,
    "ruler_tick_color": "#ff3a4a58",
    "ruler_tick_length": 6,
    "grid_color": "#22ffffff",
    "waveform_fill_color": "#aa2b4a63",
    "waveform_border_color": "#ff2b4a63",
    "cursor_color": "#ff4fc3f7",
    "cursor_head": "custom",
    "cursor_head_svg": "head",
    "cursor_head_size": 12,
    "cursor_badge_color": "#cc4fc3f7",
    "cursor_time_background_color": "#ff4fc3f7",
    "cursor_time_text_color": "#ff0f1418",
    "offscreen_arrow_color": "#ff4fc3f7",
    "repeat_area_color": "#404fc3f7"
  },
  "boxes": {
    "subtitle": {
      "top": 55,
      "bottom": 22,
      "gap": 2,
      "fill_color": "#e6607080",
      "selected_fill_color": "#ffa8c4d8",
      "text_color": "#ffeef3f7",
      "selected_text_color": "#ff0f1418",
      "padding_x": 14,
      "padding_y": 5,
      "edge_style": "bar",
      "frame_svg": "box"
    },
    "title": {
      "fill_color": "#e6806a9c",
      "selected_fill_color": "#ffcbb8ea",
      "text_color": "#fff4efff",
      "selected_text_color": "#ff1d1530"
    }
  },
  "shapes": {
    "box": "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 40'><g data-slice='5'><rect width='100' height='40' rx='5' fill='currentColor'/></g><g data-x='left' data-slice='5 0 5 5'><path d='M5 0 H8 V40 H5 A5 5 0 0 1 0 35 V5 A5 5 0 0 1 5 0 Z' fill='speakerColor'/></g></svg>",
    "head": "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 12'><path d='M0 0 H10 V7 L5 12 L0 7 Z' fill='currentColor'/></svg>"
  },
  "options": [
    {"key": "box_color", "type": "color", "label": "Subtitle color", "default": "#ff607080",
     "sets": [{"box": "subtitle", "key": "fill_color", "part": "color"}]},
    {"key": "playhead_color", "type": "color", "label": "Cursor color", "default": "#ff4fc3f7",
     "sets": [{"key": "cursor_color", "part": "color"}, {"key": "cursor_badge_color", "part": "color"},
              {"key": "cursor_time_background_color", "part": "color"},
              {"key": "offscreen_arrow_color", "part": "color"}, {"key": "repeat_area_color", "part": "color"}]}
  ]
}
```

## Checklist before sharing

- The `id` is yours (`yourname.something`) and unchanged since the
  theme's first version; `version` went up.
- Colours are `#AARRGGBB`, alpha first.
- Selected boxes stand out from the rest, and their text reads on them.
- The playhead is visible over boxes, the waveform and the background.
- Boxes still read when they are a few pixels wide and when two touch.
- Every shape named is in `"shapes"`, and each option's `default` is what
  the settings already are.
- For a dark theme, a light counterpart (or a reason not to).
- It loads in Subtitld, and subtitld.cc draws a preview of it.
