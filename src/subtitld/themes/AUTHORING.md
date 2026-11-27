# Making a Subtitld theme

A Subtitld theme is how a subtitle or a title is drawn over the video. It
is one self-contained HTML file. This document is the whole reference: a
person or an AI should be able to write a working theme from it alone.

- [The file](#the-file)
- [The manifest](#the-manifest)
- [Fields: the theme's options](#fields-the-themes-options)
- [Slots: where the text and pictures go](#slots-where-the-text-and-pictures-go)
- [The stage](#the-stage)
- [What Subtitld sets on the page](#what-subtitld-sets-on-the-page)
- [Placing a theme](#placing-a-theme)
- [Animation](#animation)
- [What a theme may not contain](#what-a-theme-may-not-contain)
- [Fonts and pictures](#fonts-and-pictures)
- [Trying a theme](#trying-a-theme)
- [Starter files](#starter-files)
- [Examples](#examples)
- [Checklist before sharing](#checklist-before-sharing)

## The file

A theme is a single `.html` file holding:

- its **manifest**, which is JSON inside `<script type="application/json" id="subtitld-theme">`;
- its **CSS**, in `<style>`;
- its **markup**, in `<body>`;
- every **font and picture** it uses, as `data:` URLs.

The file is two things at once:

- **Opened in a web browser,** it shows itself with sample text. That is how you design it.
- **Loaded by Subtitld,** it is the template every cue is drawn in.

There are two kinds of theme, and the format is the same for both:

| `kind` | What it draws | Text |
|---|---|---|
| `subtitle` | Subtitles. Chosen for the whole project, for one cue, or with per-speaker values. | One text: the subtitle's. Subtitld can cut it into lines, words and letters for you to style. |
| `title` | Titles: lower thirds, chapter cards, credits. Each title picks its own. | As many *slots* as the theme declares: texts, pictures and lists. |

**No JavaScript.** A theme is CSS, HTML, SVG, fonts and pictures only.
Animation is done with CSS `@keyframes`.

The file must start with `<!doctype html>`; a byte-order mark and blank
space before it are allowed. It must be UTF-8 and no larger than 5 MB.

## The manifest

```html
<script type="application/json" id="subtitld-theme">
{
  "format": 1,
  "id": "yourname.newsroom",
  "kind": "title",
  "name": "Newsroom",
  "version": "26.12.1",
  "min_subtitld": "26.12",
  "author": "Your Name",
  "licence": "CC-BY-4.0",
  "description": "A name and a role, sliding in from the left.",
  "timing": {"intro": 600, "outro": 400},
  "ignores_position": false,
  "slice": "words",
  "slots": [ … ],
  "fields": [ … ],
  "fallback": { … }
}
</script>
```

Put it in `<head>`. There must be exactly one manifest. Any key not listed
here is refused.

| Key | Required | Value |
|---|---|---|
| `format` | yes | `1`. |
| `id` | yes | Lowercase letters, digits, `.`, `_` and `-`, starting with a letter or digit. Prefix it with your own name (`yourname.newsroom`) so it does not clash with someone else's. Projects refer to themes by id. |
| `kind` | yes | `"subtitle"` or `"title"`. |
| `name` | yes | What people see in Subtitld's lists. |
| `version` | no | Numbers and dots, like `"26.12.1"`. Raise it with every change you share. Where two copies of the same id meet, the higher version wins. |
| `min_subtitld` | no | The oldest Subtitld that can use the theme, like `"26.12"`. Older versions skip it. |
| `author`, `licence`, `description` | no | Text. Write the licence as an SPDX identifier where one exists (`CC-BY-4.0`, `CC0-1.0`, `MIT`). |
| `timing` | no | `{"intro": ms, "outro": ms}`, whole milliseconds from 0 to 60000. Having it is what makes a theme **animated**; see [Animation](#animation). |
| `ignores_position` | no | `true` if the theme places itself on the whole picture; see [Placing a theme](#placing-a-theme). Default `false`. |
| `slice` | no | How far text slots are cut: `"none"`, `"lines"`, `"words"` (the default) or `"chars"`. See [Slots](#slots-where-the-text-and-pictures-go). |
| `slots` | titles | The theme's texts and pictures. A subtitle theme has one slot, `main`, and can leave this out. |
| `fields` | no | The theme's options, which the user sets in Subtitld (at most 48). |
| `fallback` | no | How the theme looks in formats that cannot carry it, such as ASS and TTML. Without one, a title in this theme is left out of those formats. |

### `fallback`

Some formats cannot show HTML. When a subtitle or title is saved to ASS or
TTML, Subtitld writes this plain look instead. For a title, the title's own
values of fields with the same names (`color`, `font_family`, …) are used
over these. Allowed keys:

| Key | Value |
|---|---|
| `placement` | `top`, `upper_third`, `center`, `lower_third` or `bottom` |
| `font_family` | font name |
| `font_size` | whole number, 4 to 400, in pixels of a 1080-pixel-tall picture |
| `bold`, `italic` | `true` or `false` |
| `color`, `outline_color`, `shadow_color`, `box_color` | colour, `#rrggbb` or `#aarrggbb` |
| `box_padding` | whole number, 0 to 200 |

**Colours throughout Subtitld put alpha first.** `#aarrggbb` means
`#80000000` is half-transparent black.

## Fields: the theme's options

Fields are the settings Subtitld shows for the theme. Each one becomes a CSS
variable on the page.

```json
"fields": [
  {"key": "accent", "type": "color", "label": "Accent", "default": "#ffe5a823", "per_speaker": true},
  {"key": "size", "type": "int", "label": "Size", "default": 56, "min": 12, "max": 200, "unit": "px"},
  {"key": "box", "type": "bool", "label": "Background box", "default": true},
  {"key": "box_color", "type": "color", "label": "Box colour", "default": "#cc000000", "visible_if": "box"},
  {"key": "font", "type": "font", "label": "Font", "default": "Montserrat"},
  {"key": "corner", "type": "select", "label": "Corner", "default": "left",
   "options": [{"value": "left", "label": "Left"}, {"value": "right", "label": "Right"}]}
]
```

| Key | |
|---|---|
| `key` | Required. Lowercase letters, digits and `_`. Must be unique within the theme. |
| `type` | Required: `number`, `int`, `bool`, `select`, `text`, `color` or `font`. |
| `label` | What Subtitld shows next to the control. |
| `default` | **Required**, and must suit the type. |
| `min`, `max`, `step` | For `number` and `int`. Values outside min and max are clamped. |
| `unit` | For `number` and `int`. Text added after the number in the CSS variable, usually `"px"`, `"ms"`, `"deg"` or `"%"`. Without a unit the variable is a bare number. |
| `options` | For `select`: a list of `{"value", "label"}`. Plain values are allowed too, and are their own label. |
| `visible_if` | The key of a `bool` field. This field is shown only while that one is on. |
| `per_speaker` | Subtitle themes only. `true` lets each speaker have their own value (set in the Speakers panel). Use it for things like a colour per speaker. |

How each field reaches the page, using `"box_color"` as the example name
(underscores become hyphens):

| Type | CSS variable `--box-color` | Also an attribute on `<html>` |
|---|---|---|
| `number`, `int` | the number and its unit: `56px` | – |
| `color` | `#rrggbb` when opaque, `rgba(r, g, b, a)` otherwise | – |
| `bool` | `1` or `0` | `data-box="1"` or `data-box="0"` |
| `select` | the chosen value, unquoted: `left` | `data-corner="left"` |
| `text`, `font` | a quoted string: `"Montserrat"` | – |

Use bools and selects in selectors:

```css
:root[data-box="1"] .card { background: var(--box-color); }
:root[data-corner="right"] .card { margin-left: auto; }
```

A `text` field's variable is a quoted string, so it can only be shown
through `content:`. For real text, use a slot.

If a field is called `karaoke_color`, Subtitld's word-by-word karaoke
highlight takes its colour from it. Without one, the highlight is gold.

## Slots: where the text and pictures go

A slot is an element of your markup with `data-slot="<key>"`. For every cue,
Subtitld puts the cue's text or picture into it.

```json
"slots": [
  {"key": "main", "type": "text", "label": "Name"},
  {"key": "role", "type": "text", "label": "Role", "max_length": 80, "placeholder": "What they do"},
  {"key": "logo", "type": "image", "label": "Logo", "visible_if": "show_logo"},
  {"key": "people", "type": "list", "label": "Cast", "of": "text"}
]
```

| Key | |
|---|---|
| `key` | Required. Lowercase letters, digits and `_`. `main` is the cue's own text, and is required. |
| `type` | `text` (the default), `image` or `list`. `main` must be `text`. |
| `label` | What Subtitld shows above the slot's input. |
| `of` | For lists: `"text"`, the only kind so far. |
| `multiline`, `max_length`, `placeholder` | Hints for the input. Text slots only. |
| `default` | Text for a text slot, a list of texts for a list slot. Shown until the user types their own. |
| `visible_if` | The key of a `bool` field. While it is off, the slot is left empty. Not allowed on `main`. |

The rules that tie markup and manifest together:

- Every slot in the manifest except `main` needs an element with that
  `data-slot`, and every `data-slot` in the markup needs a slot in the manifest.
- If the markup has any `data-slot`, it must have `data-slot="main"`.
- A **subtitle** theme has only `main`. A **title** theme can have up to 24 slots.

### A theme with no slots in its markup

If the markup has no `data-slot` at all, Subtitld ignores the markup (it
is only a sample for the browser). It puts the cue on its own stage instead,
as a `<div class="cue">` holding the text. You style `.cue`. The built-in
Classic theme works this way.

Its text is **not sliced**: there are no `.line`, `.word` or `.char`
elements. To animate words or letters, put `data-slot="main"` in the markup.

### A theme with slots

The markup in `<body>` is the template. For each cue, Subtitld:

1. copies the whole body markup into a `<div class="cue subtitld-frame">`;
2. fills the slots;
3. places that div where the cue goes (see [Placing a theme](#placing-a-theme)).

Inside `<body>`, write the markup exactly as it should be drawn, with sample
text in the slots so the file looks right in a browser.

**Text slots.** The text arrives as HTML: `<br>` between lines, and the
cue's own formatting as `<b>`, `<i>`, `<u>`, `<s>`, `<sub>`, `<sup>`,
`<ruby>` and `<span style="…">`. It is then cut as far as `slice` says:

```html
<h1 data-slot="main" style="--lines: 2; --words: 4; --chars: 0">
  <span class="line" style="--i: 0"><span class="word" style="--i: 0">Maria</span> <span class="word" style="--i: 1">Souza</span></span>
  <span class="line" style="--i: 1"><span class="word" style="--i: 2">Field</span> <span class="word" style="--i: 3">reporter</span></span>
</h1>
```

- Each level includes the ones above it. `"chars"` gives `.line`, then
  `.word` in it, then `.char` in that; `"words"` gives lines and words;
  `"lines"` gives lines only.
- `.line` elements are `display: block` within a slot. Each has `--i`, its
  number from 0.
- `.word` and `.char` elements have `--i` counted across the whole slot.
  Use it to stagger: `animation-delay: calc(var(--i) * 60ms)`.
- The slot itself gets `--lines`, `--words` and `--chars`, the counts.
- With `"chars"`, each word holds `.char` elements, one per character as
  people see them (an emoji or a letter with its accent is one).
- Formatting is kept. A word that was bold is still inside its `<b>`.
- The cue's own colours and formatting are written inline on the `main`
  slot's element, so they win over the theme's CSS.
- Every text slot is sliced, not only `main`.
- Because lines are blocks, anything you add to the slot element itself
  with `::before` or `::after` (quote marks, a dash) lands on a line of its
  own. Put it on the first or last line instead:
  `.quote .line:first-child::before { content: "“"; }` and
  `.quote .line:last-child::after { content: "”"; }`.

**Image slots.** The picture arrives as a `data:` URL:

- on an `<img data-slot>` as its `src`;
- on an SVG `<image data-slot>` as its `href`;
- on any other element as a CSS variable `--slot-image: url("…")`, so you
  can use `background-image: var(--slot-image)`.

**List slots.** The slot element's **first child element is the pattern**.
For each item, the pattern is copied, and the item's text goes into its
`[data-item]` element (or into the copy itself, if it has none). Each copy
gets `--i`, and the slot gets `--items`. Item text is sliced like a text
slot's.

```html
<ul class="credits" data-slot="people">
  <li><span class="role">Cast</span> <span data-item>Sample Name</span></li>
</ul>
```

**Empty slots** are emptied and get a `data-empty` attribute. Hide what
should not show:

```css
.role[data-empty], .logo[data-empty] { display: none; }
```

## The stage

Subtitld draws every cue on a stage **1080 pixels tall**, whatever the
video's real size. Its width follows the video's shape: 1920 for 16:9,
1080 for a square video, 608 for a vertical 9:16 one. Write every length in
`px` as if the picture were 1080 pixels tall. Subtitld scales the result
to the real video, so one theme works for HD, 4K, vertical and square.

Inside the stage is the **title-safe area**, the margin the user sets in
Subtitld (10% by default). Cues are placed inside it.

Do not size things with `vw`, `vh` or `%` of the page. Use `px`, `em`, or
the variables below.

Subtitld forces `html` and `body` to be transparent, so a background you
give them shows only in a browser. Use it for a sample backdrop.

## What Subtitld sets on the page

On the root element (`<html>`, which `:root` selects):

| Variable | Value |
|---|---|
| every field | as [Fields](#fields-the-themes-options) shows |
| `--stage-width`, `--stage-height` | the stage's size, like `1920px` and `1080px` |
| `--safe-left`, `--safe-right`, `--safe-top`, `--safe-bottom` | the title-safe margins, in px |
| `--cue-duration` | the cue's length, like `3500ms` |
| `--cue-time` | how far into the cue this picture is, like `1200ms` |

It also sets:

- a `data-…` attribute for every bool and select field;
- the class `theme-out` on `<html>` during the outro (see [Animation](#animation)).

Give every variable a default in your own `:root { … }` rule, so the file
also looks right in a browser. Subtitld's values override those defaults.

## Placing a theme

**Subtitles** go where the cue asks to be. By default that is the bottom
centre of the title-safe area. A cue can ask for any of nine places (`{\an8}`
is top centre) or for margins of its own; the user can also drag it.
Subtitld wraps the cue in a flex box that does this. Your `.cue` or frame
only needs to style itself, not position itself.

**Titles** go where the `placement` field says. Give a title theme a
`select` field called `placement` with any of these values, and Subtitld
places the title by it:

| Value | Where |
|---|---|
| `top` | top centre |
| `upper_third` | upper left |
| `center` | the middle |
| `lower_third` | lower left |
| `bottom` | bottom centre |

A title with no `placement` field sits in the middle. A title that asks for
a place in its text (`{\an3}`) goes there instead.

**A theme that places itself** sets `"ignores_position": true`. Its frame
then covers the whole stage (`position: absolute; inset: 0`), and the theme
lays itself out using `--safe-*` and `--stage-*`. Use this for full-screen
cards, credits, or anything that is not a block of text. Only themes with
slots can do this.

**Room around it.** Subtitld cuts the picture to the box of every element
in the cue, plus 32 pixels on each side. A glow, shadow or movement
reaching further than that is clipped. If an effect needs more room, give
the element padding or an invisible box that covers it.

## Animation

Animated themes use CSS `@keyframes` animations. Subtitld does not play
them in real time. For every picture it draws, it **pauses every animation
and moves it to the cue's own time**. A picture drawn 1.2 s into a cue
shows each animation exactly as it is 1.2 s after the cue began, in the
preview and in the exported video alike. That makes two rules:

- An animation's clock starts when the cue starts. `animation-delay` is the
  moment it starts; `animation-duration` is how long it lasts.
- **Use `animation-fill-mode: both`** (or `both` in the `animation`
  shorthand), so an element waits in its first keyframe before its delay
  and keeps its last keyframe after it ends.

Only `@keyframes` animations are seeked. CSS transitions, SMIL (`<animate>`)
and anything time-based in JavaScript are not available.

For a change that happens at once (a letter appearing, as on a typewriter),
use `steps(1, end)` as the timing function, or a very short duration:

```css
.char { animation: type 1ms steps(1, end) both; animation-delay: calc(100ms + var(--i) * 30ms); }
@keyframes type { from { opacity: 0; } }
```

### `timing`, intro and outro

`"timing": {"intro": 700, "outro": 400}` says how long the entrance and the
exit take, in milliseconds.

- **It is what makes a theme animated.** Without `timing`, Subtitld draws
  every cue once, at its first moment: an animation in a theme without
  `timing` is stuck at its first frame.
- **The intro** is the first `intro` ms of the cue. Put your entrance
  animations there.
- **The outro** is the last `outro` ms. When it begins, Subtitld adds the
  class `theme-out` to `<html>`. Animations that start because of that class
  (`.theme-out .card { animation: … }`) have their clock start at the
  outro's start.
- **Between them,** the cue should be still. When a video is exported,
  Subtitld draws a picture for every frame of the intro and outro, but only
  **one** picture for the middle. Something still moving after the intro
  freezes in the export. A slow loop in the middle is the one thing a
  theme cannot do.
- In Subtitld's timeline, the intro and outro show as shaded ends of the
  cue. A cue shorter than intro plus outro is marked in red.

A full example:

```css
.card { animation: card-in 600ms cubic-bezier(.2, .7, .2, 1) both; }
.word { display: inline-block; animation: word-in 350ms ease-out both;
        animation-delay: calc(150ms + var(--i) * 60ms); }
.theme-out .card { animation: card-out 400ms ease-in both; }

@keyframes card-in  { from { transform: translateX(-30px); opacity: 0; } }
@keyframes word-in  { from { transform: translateY(0.4em); opacity: 0; } }
@keyframes card-out { to   { transform: translateX(-40px); opacity: 0; } }
```

Keep the last staggered word's delay plus its duration within the intro.
Here, with four words, that is 150 + 3 × 60 + 350 = 680 ms, so the intro
should be at least 700.

`--cue-time` and `--cue-duration` are on the page too. With `calc()` they can
place something along the cue's length without an animation.

## What a theme may not contain

Subtitld checks every theme before using it, and subtitld.cc runs the same
check. A file that fails is refused **with every reason listed**; it is
never silently changed.

**Elements.** Allowed: `html`, `head`, `body`, `title`, `meta`, `style`, the
manifest `script`, `div`, `span`, `p`, `h1`–`h6`, `br`, `hr`, `wbr`,
`strong`, `em`, `b`, `i`, `u`, `s`, `small`, `sub`, `sup`, `mark`, `q`,
`cite`, `abbr`, `time`, `bdi`, `bdo`, `ruby`, `rt`, `rp`, `ul`, `ol`, `li`,
`figure`, `figcaption`, `blockquote`, `section`, `header`, `footer`,
`article`, `aside`, `main`, `nav`, `img`, and inline SVG (shapes, text,
gradients, masks, clip paths, patterns, markers, `use`, `image`,
`foreignObject` and every filter primitive). Everything else is refused.
In particular:

| Refused | Why |
|---|---|
| any `<script>` but the manifest | themes have no scripts |
| `<animate>`, `<set>`, `<animateMotion>`, `<animateTransform>` | SMIL cannot be seeked: use CSS animations |
| `<link>`, `<iframe>`, `<object>`, `<embed>`, `<base>` | they load things from outside the file |
| `<video>`, `<audio>`, `<canvas>`, form controls, MathML | not part of a theme's picture |

**Attributes.**

- No event handlers (`onload`, `onclick`, any `on…`).
- No `srcset` and no `http-equiv`.
- `<meta>` may only have `charset`, or `name` and `content`.
- Every URL (`src`, `href`, `xlink:href`, …) must be a `data:` URL of a
  picture (PNG, JPEG, GIF, WebP, AVIF or SVG) or a `#fragment` inside the file.

**CSS**, in `<style>` and in `style=""` alike:

- `url()` must hold a `data:` picture, a `data:` font (WOFF2, WOFF, TTF or
  OTF) or a `#fragment`.
- No `@import`, `expression()`, `-moz-binding`, `behavior:` or `javascript:`.
- No markup inside a stylesheet.

**Other.**

- No `<![CDATA[`.
- No comment written `<!-->` or `<!--->`; write `<!-- … -->`.
- No processing instructions.

As a final guard, Subtitld draws themes with a policy that blocks scripts
and network access, whatever a file contains.

## Fonts and pictures

**Fonts.** A theme can use:

- **Montserrat**, which ships with Subtitld;
- any font installed on the computer, by name, though it then looks right
  only where that font is installed;
- **its own font, embedded.** This is the reliable way:

```css
@font-face {
  font-family: "My Display";
  src: url("data:font/woff2;base64,d09GMgABAAAAA…") format("woff2");
  font-weight: 700;
}
```

Embed only fonts whose licence allows redistribution, such as the SIL Open
Font License or Apache. Most Google Fonts qualify. Subset a font to the
characters you need to keep the file small; the whole theme must stay
under 5 MB.

**Pictures** go in as `data:` URLs: `<img src="data:image/png;base64,…">`,
`url("data:image/svg+xml;base64,…")`, or inline `<svg>`. A picture the user
chooses for a title goes through an image slot instead.

## Trying a theme

**Check it** with Subtitld itself:

```bash
subtitld --check-theme mytheme.html
```

This prints every problem, or what Subtitld reads in the file: kind,
slots, fields, whether it is animated, and whether it has a fallback.
To see it drawn, add `--preview`:

```bash
subtitld --check-theme mytheme.html --preview sheet.png
```

This writes `sheet.png`: the theme drawn by the same renderer as the
preview and video export, on a checked backdrop of the stage's shape.
An animated theme is drawn at eight moments: 0%, 25%, 50% and 75% into its
intro, the middle, the start and middle of its outro, and its last frame.
A theme without `timing` is drawn once.

Unless you say otherwise, the preview uses:

- for a subtitle: "A sample subtitle," / "in two lines of text";
- for a title's `main`: "Sample title";
- for other text slots: their `default`, else their `placeholder`, else
  "Sample" and the slot's label;
- for lists: their `default`, else three items;
- for image slots: a plain yellow disc;
- every field's default, including `placement`, so a title is placed as
  it would be.

The sample text inside your own markup is for browsers only; the preview
never uses it. Try your own text and values with these options:

| Option | |
|---|---|
| `--text "…"` | The cue's text. Write `\n` for a new line. |
| `--slot key="…"` | A title's other text slot. For a list slot, separate items with `\|`. |
| `--set key=value` | A field's value: `--set show_logo=false`, `--set placement=top`, `--set font_size=80`, `--set color='"#ffff0000"'`. The value is read as JSON where it can be, as text otherwise. |

```bash
subtitld --check-theme quote.html --preview long.png \
  --text "The best way to predict the future\nis to invent it" \
  --slot author="Alan Kay" --set show_portrait=false
```

Run from source, the command is `python -m subtitld --check-theme …`.

**Use it in Subtitld** by copying the file into your themes folder and
restarting Subtitld:

| System | Folder |
|---|---|
| Linux | `~/.local/share/subtitld/themes/` |
| Windows | `%LOCALAPPDATA%\subtitld\subtitld\themes\` |
| macOS | `~/Library/Application Support/subtitld/themes/` |

A subtitle theme then appears in the subtitle style list and in a
subtitle's Theme menu. A title theme appears in a title's theme list.

**Share it with a project.** Turn on *Installed subtitle and title themes*
under Settings → USFX. A saved `.usfx` then carries the themes it uses, and
opens with them on any computer.

## Starter files

### Subtitle theme

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>My subtitles</title>
<script type="application/json" id="subtitld-theme">
{
  "format": 1,
  "id": "yourname.my_subtitles",
  "kind": "subtitle",
  "name": "My subtitles",
  "version": "1.0.0",
  "author": "Your Name",
  "licence": "CC0-1.0",
  "description": "White text on a soft dark band.",
  "slice": "lines",
  "fields": [
    {"key": "font_size", "type": "int", "label": "Size", "default": 48, "min": 12, "max": 200, "unit": "px"},
    {"key": "color", "type": "color", "label": "Colour", "default": "#ffffffff", "per_speaker": true},
    {"key": "band_color", "type": "color", "label": "Band", "default": "#99000000"}
  ],
  "fallback": {"font_size": 48, "color": "#ffffffff", "box_color": "#99000000", "box_padding": 10}
}
</script>
<style>
  :root { --font-size: 48px; --color: #ffffff; --band-color: rgba(0, 0, 0, 0.6); }
  /* Only in a browser: a backdrop and the sample where Subtitld would put it. */
  html { background: #3a3a3a; }
  body { min-height: 100vh; margin: 0; display: flex; align-items: flex-end; justify-content: center; padding-bottom: 10vh; box-sizing: border-box; }

  .text {
    font-family: "Montserrat", sans-serif;
    font-size: var(--font-size);
    font-weight: 600;
    line-height: 1.25;
    color: var(--color);
    text-align: center;
  }
  /* Each line on a band of its own (slice "lines": Subtitld makes them). */
  .text .line { display: block; width: fit-content; margin: 0 auto; padding: 0.1em 0.5em; background: var(--band-color); }
</style>
</head>
<body>
  <div class="text" data-slot="main">A sample subtitle,<br>in two lines</div>
</body>
</html>
```

### Title theme

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>My title</title>
<script type="application/json" id="subtitld-theme">
{
  "format": 1,
  "id": "yourname.my_title",
  "kind": "title",
  "name": "My title",
  "version": "1.0.0",
  "author": "Your Name",
  "licence": "CC0-1.0",
  "description": "A heading and a line under it, fading up.",
  "timing": {"intro": 600, "outro": 300},
  "slice": "none",
  "slots": [
    {"key": "main", "type": "text", "label": "Heading"},
    {"key": "subheading", "type": "text", "label": "Subheading", "max_length": 100}
  ],
  "fields": [
    {"key": "placement", "type": "select", "label": "Place", "default": "center",
     "options": [{"value": "top", "label": "Top"}, {"value": "center", "label": "Middle"},
                 {"value": "lower_third", "label": "Lower third"}, {"value": "bottom", "label": "Bottom"}]},
    {"key": "color", "type": "color", "label": "Colour", "default": "#ffffffff"},
    {"key": "accent", "type": "color", "label": "Line", "default": "#ffe5a823"}
  ],
  "fallback": {"placement": "center", "font_size": 72, "bold": true, "color": "#ffffffff"}
}
</script>
<style>
  :root { --color: #ffffff; --accent: #e5a823; }
  html { background: #3a3a3a; }
  body { min-height: 100vh; margin: 0; display: flex; align-items: center; justify-content: center; }

  .card { font-family: "Montserrat", sans-serif; color: var(--color); text-align: center;
          animation: rise 600ms cubic-bezier(.2, .7, .2, 1) both; }
  .heading { margin: 0; font-size: 80px; font-weight: 800; line-height: 1.05; }
  .rule { height: 6px; width: 160px; margin: 18px auto; background: var(--accent);
          transform-origin: 50% 50%; animation: grow 500ms 100ms ease-out both; }
  .sub { margin: 0; font-size: 36px; font-weight: 500; }
  .sub[data-empty] { display: none; }
  .theme-out .card { animation: fade 300ms ease-in both; }

  @keyframes rise { from { opacity: 0; transform: translateY(24px); } }
  @keyframes grow { from { transform: scaleX(0); } }
  @keyframes fade { to { opacity: 0; } }
</style>
</head>
<body>
  <div class="card">
    <h1 class="heading" data-slot="main">Chapter one</h1>
    <div class="rule"></div>
    <p class="sub" data-slot="subheading">Where it all began</p>
  </div>
</body>
</html>
```

## Examples

### Karaoke-style words

A subtitle theme that lights its words one after another. It slices into
words, and each word's delay follows its `--i`.

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Word light</title>
<script type="application/json" id="subtitld-theme">
{
  "format": 1,
  "id": "example.word_light",
  "kind": "subtitle",
  "name": "Word light",
  "version": "1.0.0",
  "licence": "CC0-1.0",
  "description": "Each word lights up after the one before.",
  "timing": {"intro": 1200, "outro": 250},
  "slice": "words",
  "fields": [
    {"key": "font_size", "type": "int", "label": "Size", "default": 54, "min": 12, "max": 200, "unit": "px"},
    {"key": "dim", "type": "color", "label": "Before", "default": "#99ffffff"},
    {"key": "lit", "type": "color", "label": "Lit", "default": "#ffffd23f", "per_speaker": true},
    {"key": "step", "type": "int", "label": "Time per word", "default": 120, "min": 20, "max": 400, "unit": "ms"}
  ],
  "fallback": {"font_size": 54, "color": "#ffffd23f", "bold": true}
}
</script>
<style>
  :root { --font-size: 54px; --dim: rgba(255, 255, 255, 0.6); --lit: #ffd23f; --step: 120ms; }
  html { background: #3a3a3a; }
  body { min-height: 100vh; margin: 0; display: flex; align-items: flex-end; justify-content: center; padding-bottom: 10vh; box-sizing: border-box; }

  .text { font-family: "Montserrat", sans-serif; font-weight: 800; font-size: var(--font-size);
          line-height: 1.2; text-align: center; color: var(--dim);
          text-shadow: 0 2px 6px rgba(0, 0, 0, 0.8); }
  .word { animation: light 200ms ease-out both; animation-delay: calc(var(--i) * var(--step)); }
  .theme-out .text { animation: out 250ms ease-in both; }

  @keyframes light { to { color: var(--lit); } }
  @keyframes out { to { opacity: 0; } }
</style>
</head>
<body>
  <div class="text" data-slot="main">Every word lights up in turn</div>
</body>
</html>
```

The intro must cover the last word: with ten words at 120 ms, that is
9 × 120 + 200 = 1280 ms. Words beyond the intro still light up in the
preview, but freeze in an exported video. Pick an intro for the longest
lines you expect, or keep the step short.

### Lower third

Subtitld's built-in **Lower third** (`themes/lower_third.html` in
Subtitld's folder) is a full title example:

- three slots: a name, a role, and a logo that a bool field can hide;
- an accent bar that grows, a box that opens, and words rising in one by one;
- an outro that slides it all away;
- a `placement` field and a fallback.

Read it alongside this document.

### Credits from a list

A title that places itself and lists names from a list slot.

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Credits</title>
<script type="application/json" id="subtitld-theme">
{
  "format": 1,
  "id": "example.credits",
  "kind": "title",
  "name": "Credits",
  "version": "1.0.0",
  "licence": "CC0-1.0",
  "description": "A heading and a column of names, appearing one after another.",
  "timing": {"intro": 1500, "outro": 500},
  "ignores_position": true,
  "slice": "none",
  "slots": [
    {"key": "main", "type": "text", "label": "Heading"},
    {"key": "names", "type": "list", "of": "text", "label": "Names", "default": ["Ana Lima", "Bruno Reis", "Chen Wei"]}
  ],
  "fields": [
    {"key": "color", "type": "color", "label": "Colour", "default": "#ffffffff"},
    {"key": "backdrop", "type": "color", "label": "Backdrop", "default": "#e6101418"}
  ]
}
</script>
<style>
  :root { --color: #ffffff; --backdrop: rgba(16, 20, 24, 0.9);
          --safe-top: 108px; --safe-bottom: 108px; }
  html { background: #3a3a3a; }
  body { margin: 0; }

  .screen { position: absolute; inset: 0; background: var(--backdrop); color: var(--color);
            display: flex; flex-direction: column; align-items: center; justify-content: center;
            padding: var(--safe-top) 0 var(--safe-bottom); box-sizing: border-box;
            font-family: "Montserrat", sans-serif; animation: in 400ms ease-out both; }
  .heading { margin: 0 0 40px; font-size: 42px; font-weight: 500; letter-spacing: 0.2em; text-transform: uppercase; }
  .names { list-style: none; margin: 0; padding: 0; text-align: center; }
  .names li { font-size: 56px; font-weight: 700; line-height: 1.4;
              animation: in 400ms ease-out both; animation-delay: calc(400ms + var(--i) * 150ms); }
  .theme-out .screen { animation: out 500ms ease-in both; }

  @keyframes in { from { opacity: 0; transform: translateY(16px); } }
  @keyframes out { to { opacity: 0; } }
</style>
</head>
<body>
  <div class="screen">
    <h2 class="heading" data-slot="main">Cast</h2>
    <ul class="names" data-slot="names"><li data-item>Sample Name</li></ul>
  </div>
</body>
</html>
```

Because it sets `ignores_position`, the frame covers the whole stage and
`.screen` fills it. With a list of n names, the last one settles at
400 + (n − 1) × 150 + 400 ms. Keep the intro at least that long for the
longest list you plan to use.

## Checklist before sharing

1. `subtitld --check-theme` reports no problems.
2. The `--preview` sheet looks right at every moment. The intro's first
   frame may be empty; the middle must be complete.
3. Every animation has `both` fill, and finishes within the intro or within
   the outro.
4. Nothing is cut at the edges: effects stay within 32 px of an element's
   box.
5. It works with one short line and with two long lines (`--text`), and,
   for titles, with empty optional slots and every `placement` (`--set`).
6. Every length is in px of a 1080-pixel-tall picture, and the file looks
   right in a browser too.
7. Fonts are embedded and licensed for redistribution, and the licence is
   in the manifest.
8. `fallback` is set if the theme should still appear in ASS and TTML exports.
9. `version` is raised since the last copy you shared.
