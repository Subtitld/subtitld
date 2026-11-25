# Subtitle and title themes

Subtitles and titles get their look from themes: HTML pages the subtitle
renderer fills with a cue's text, with options the user sets and animations
that play along the cue. Themes are shared on subtitld.cc the way timeline
themes are.

Today there are two half-steps toward this: subtitle styles
(`modules/subtitle_styles.py`, a folder with `style.json` + `style.html`,
settings in the app config) and title themes (`modules/title_themes.py`, a
folder with `theme.json` + `theme.html`, settings on each title). Both become
one kind of file.

## Decisions taken

- **One self-contained HTML file is a theme.** The manifest, the CSS, the
  markup and every asset are in it. Opened in a browser it shows itself with
  sample text; opened in Subtitld it is the template the renderer fills.
- **Two kinds, one format.** The manifest's `kind` is `subtitle` or `title`.
- **Subtitles have one text slot.** The app slices it into lines, words and
  letters, so a theme can style or animate any of them.
- **Titles declare their slots**, as many as they need: text, image and list
  slots. `main` is required and is the cue's own text.
- **No JavaScript in themes.** CSS, SVG, fonts and images only. The app's own
  script (`host.js`) still runs in the page.
- **Animations follow the cue's time**, never the clock: paused, and seeked to
  the moment being drawn, in the preview and in burn-in.
- **The cue's own formatting and position win** over the theme (bold, italic,
  colored runs, `{\anN}`, a dragged position, file styling from TTML/ASS). A
  theme can declare that it ignores position.
- **Subtitle themes are per project**, with a per-cue override. Option values
  are stored in the project, per theme, shared by every subtitle on that
  theme. Titles keep their own values.
- **Per-speaker values** only for fields the theme marks `per_speaker`, set in
  the speakers panel.
- **Images are allowed in titles.**
- **A project names its themes by id and version.** When one is missing,
  Subtitld offers to download it from subtitld.cc and draws the default
  meanwhile. Embedding the theme file in the USFX is an option the user can
  turn on.
- **Designed against a 1080-pixel-tall stage** (as `host.js` does now), as
  wide as the video's aspect ratio, with `--scale` and safe-area variables, so
  one theme works for 4K, vertical and square video.
- **Fonts are bundled** and need a licence that allows redistribution (OFL
  and the like). subtitld.cc sets a package size limit.
- **Today's subtitle styles become subtitle themes.** Classic, the current
  look, is the built-in default, so existing projects look the same.
- **Authors get a reference.** A theme authoring document, written so a
  person or an AI can make a theme from it alone, ships with the app and on
  subtitld.cc.

## The file

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Newsroom</title>

<script type="application/json" id="subtitld-theme">
{
  "format": 1,
  "id": "jonata.newsroom",
  "kind": "title",
  "name": "Newsroom",
  "version": "26.12.1",
  "min_subtitld": "26.12",
  "author": "…",
  "licence": "CC BY 4.0",
  "timing": {"intro": 400, "outro": 300},
  "ignores_position": false,
  "slots": [
    {"key": "main", "type": "text", "label": "Name"},
    {"key": "role", "type": "text", "label": "Role", "max_length": 60},
    {"key": "logo", "type": "image", "label": "Logo", "visible_if": "show_logo"}
  ],
  "fields": [
    {"key": "accent", "type": "color", "default": "#ffe5a823", "per_speaker": true},
    {"key": "show_logo", "type": "bool", "default": true}
  ],
  "fallback": {"placement": "lower_third", "color": "#ffffffff", "font_size": 54}
}
</script>

<style>
  @font-face { font-family: Inter; src: url(data:font/woff2;base64,…); }
  :root { --accent: #e5a823; }                 /* defaults, for a plain browser */
  .card { animation: slide-in .4s ease-out both; }
  .theme-out .card { animation: slide-out .3s ease-in both; }
  [data-slot="role"]:empty { display: none; }
  @keyframes slide-in  { from { transform: translateX(-40px); opacity: 0 } }
  @keyframes slide-out { to   { transform: translateX(-40px); opacity: 0 } }
</style>
</head>
<body>
  <div class="card">
    <img data-slot="logo" src="data:image/svg+xml;base64,…">
    <h1 data-slot="main">Maria Souza</h1>
    <p data-slot="role">Field reporter</p>
  </div>
</body>
</html>
```

### Manifest

In `<script type="application/json" id="subtitld-theme">`: inert data, never
run. Keys:

| Key | |
|---|---|
| `format` | The file format's version, 1. |
| `id` | Lowercase, dotted. subtitld.cc gives store themes `cc.<share id>`, as with timeline themes. |
| `kind` | `subtitle` or `title`. |
| `name`, `version`, `min_subtitld`, `author`, `licence`, `description` | As timeline themes. Version `YY.MM.N`. |
| `timing` | `intro` and `outro` in ms: drawn on the timeline, and a cue shorter than both gets a warning. Omitted: not animated, rendered once per cue. |
| `ignores_position` | The theme places itself; position controls are off for it. |
| `slots` | Titles only (a subtitle theme has `main` implicitly). |
| `fields` | Options, the field types subtitle styles have now: `number`, `int`, `bool`, `select`, `text`, `color`, `font`; `visible_if`, `per_speaker`. |
| `fallback` | The look for formats that can't carry the theme (ASS, TTML): font, size, color, box, placement. Omitted: burn-in only. |

### Slots

Any element with `data-slot="<key>"`. What the author writes inside is the
sample the browser shows; Subtitld replaces it.

- **`text`:** filled with `.line > .word > .char` spans. Each has `--i` (its
  index), and timed words get `--k-start` / `--k-length` as `host.js` gives
  karaoke pieces now. Hints: `multiline`, `max_length`, `placeholder`,
  `default`.
- **`image`:** the element's `src` is replaced by the picked image, stored in
  the project.
- **`list`:** `{"type": "list", "of": "text"}`. The element's first child is
  the item's pattern, repeated once per item (credits, bullet points).
- An empty optional slot is left empty, so `:empty` hides it. A slot can
  follow a bool field with `visible_if`.
- On a theme switch, slots map by key. Text in a slot the new theme lacks
  stays on the cue, unshown.
- Text slots are translatable; image slots are shared across languages.

### What Subtitld sets on the page

- Fields as CSS variables on the root (`--accent`), bools as attributes too
  (`data-show-logo="1"`), as subtitle styles do now.
- `--stage-width`, `--stage-height` (1080px), `--safe-left/top/right/bottom`,
  `--cue-duration`, `--cue-time`. No `--scale`: on the stage a pixel is
  already 1/1080 of the picture's height, and host.js scales the stage.
- On a text slot: `--lines`, `--words`, `--chars`; on each `.line`, `.word`,
  `.char` its `--i`. How far text is cut is the manifest's `slice` (`none`,
  `lines`, `words` (default), `chars`). On a list slot `--items`, on each
  item `--i`; the item's text goes in its `[data-item]`.
- An empty slot gets `data-empty`. An image slot that isn't an `<img>` gets
  `--slot-image`.
- `data-kind`, `data-speaker`, `data-align` (the cue's `{\anN}`), and
  `.theme-out` during the last `timing.outro` ms.

The theme's `:root` defaults are what a plain browser shows.

### Slotted or not

A theme whose markup has `data-slot="main"` gets its markup back for each
cue, slots filled (and then every slot it declares must be marked). A theme
with no `data-slot` has its body taken out (a sample for browsers) and the
cue put on Subtitld's own stage in a `.cue`, as Classic and Plain do. The
file must start with `<!doctype html>`.

### Assets

Fonts in `@font-face`, images in `src`, SVG inline: all `data:` URIs or inline
markup. Nothing outside the file is reachable.

## Safety

The same check runs on upload to subtitld.cc and on install in Subtitld,
including a theme embedded in a USFX:

- Read with Python's `html.parser`, which follows the HTML5 tokenizer (lxml's
  HTML parser mis-nests self-closed SVG elements). Allowlist elements and
  attributes. Reject `<script>` (except the manifest), `on*` attributes,
  `<iframe>`, `<object>`, `<embed>`, `<link>`, `<base>`, `<meta http-equiv>`,
  SMIL (it can't be seeked), `javascript:` and remote or relative URLs.
  Where `html.parser` and a browser disagree (`<!-->`, CDATA in HTML), the
  file is refused outright.
- In CSS, `url()` and `@import` only for `data:` and `#fragment`.
- The file is **never rewritten**: parsing and serializing again would
  change it. Both subtitld.cc and Subtitld **refuse** a file with problems,
  listing every reason (Subtitld then draws the default theme).
- The renderer loads a copy of the file (`themes.served()`, in the cache)
  with a Content Security Policy `<meta>` right after the doctype: no
  scripts, nothing but `data:` images and fonts and Subtitld's own fonts.
  `host.js` is injected by QtWebEngine and still runs (tested in M2).
- Size limit per file (a few MB), checked before parsing.

## The app

### Modules

- **`modules/themes.py`** (new, parsing done in M1): parse and check a theme file, the manifest
  dataclass, available themes by kind (built-in folder + the user's data
  folder), values for a project/cue/speaker, CSS variables and attributes.
  `subtitle_styles.py` and `title_themes.py` fold into it.
- **`modules/theme_sanitize.py`** (new, done in M1): the allowlist check above, shared
  with subtitld.cc (copied there or vendored, decided at that milestone).
- **`modules/theme_store.py`:** extended from timeline themes to the two new
  kinds: browse, install, update, and download a project's missing theme.

### Project data

- `SUBTITLE.theme` (the subtitle theme's id and version) and
  `SUBTITLE.theme_values` ({theme id: {key: value}}), a cue's
  `theme` override, the speakers' per-speaker values, and the titles'
  `title_theme` / `title_settings` / `title_slots`.
- USFX: in the `subtitldcc` namespace, as marks' categories are. An embedded
  theme goes in as escaped text under its id and version; reading one
  installs it into a project-only cache, not the user's themes.
- Migration: the subtitle style settings in `CONFIG['subtitle_style']` become
  the default values new projects start with; an old project with none gets
  Classic with those values.

### Renderer

- `host.js` learns `data-slot`: fills text slots with sliced markup, image
  and list slots, sets the variables, adds `.theme-out`. Its current `.cue`
  / `#safe` stage stays for themes without slots (Classic).
- Animated themes are rendered per frame, as animated styles already are
  (`animated` in `style.json` today becomes "has `timing`").
- Per-template views exist already (title themes). A subtitle theme per cue
  means more templates at once: raise `OTHER_TEMPLATES`, or keep them by use.

### Interface

- The editor's Theme dropdown (UI only today) lists subtitle themes: the
  project's, or this cue's override.
- A project-level theme choice with its options (where the Interface
  panel's subtitle style is today).
- Titles: the slot fields stacked in the editor, scrolling when many; an
  image picker; the settings dialog stays for options.
- Speakers panel: the `per_speaker` fields.
- Timeline: intro/outro zones drawn on animated cues; the warning for cues
  shorter than both.
- Theme browser: the timeline themes' browser, with a kind filter.
- "Reload from file": point at a theme file on disk and it reloads when the
  file changes, for authors.
- Project properties: "Embed themes in the project file".

### Export

- Burn-in: as now, animated themes per frame.
- ASS / TTML: the theme's `fallback`, as `title_themes.as_subtitle` does for
  Plain now. Without one, the theme is burn-in only and the export dialog
  says so.

## subtitld.cc

- Upload of theme files beside timeline themes: the safety check, the font
  licence check, the size limit.
- Preview: rendered by Subtitld in bwrap as timeline themes are, as a short
  looping video for animated themes, with the sample text.
- A preview page with sliced sample text and a time scrubber, so an author
  sees word and letter animations a plain browser can't show.
- API for the app's browser and for downloading a project's missing theme by
  id and version.

## Documentation

A theme authoring reference, complete enough that a person or an AI can make
a valid theme from it alone:

- the file layout, every manifest key, every slot type and hint;
- the markup Subtitld writes into slots (`.line`, `.word`, `.char`, the
  variables on each) and everything set on the root;
- the animation rules (paused and seeked, `timing`, `.theme-out`, word times);
- what is rejected and why;
- the stage, `--scale`, safe areas, aspect ratios;
- a starter file for each kind, and a few complete examples (a plain
  subtitle, karaoke, a lower third, credits as a list);
- a checklist before uploading.

It lives in the repo (`docs/themes.md`, shipped with the app), as a page on
subtitld.cc, and as one plain file that can be handed to an AI as is.

## Milestones

1. **Format and check.** (done) `themes.py` parse + `theme_sanitize.py`, with tests
   for good files and every rejected construct.
2. **Renderer.** (done) `host.js` slots, variables, `.theme-out`; the CSP; Classic
   and Plain converted to the new file; pixel-compare against today's output.
3. **Subtitle themes in the project.** (done) Project/cue/speaker values, USFX
   read/write, migration from `CONFIG['subtitle_style']`, the editor
   dropdown and project choice.
4. **Title slots.** (done; built-in Lower third added as the first slotted theme) Slot fields in the editor, image and list slots,
   translation per slot, mapping on theme switch.
5. **Animation.** (done) Timeline intro/outro zones, the short-cue warning; measure
   burn-in speed on a real video with an animated theme and decide whether
   it needs work before going on.
6. **Export.** (done) Fallbacks for ASS/TTML, the export dialog notice.
7. **Embedding and missing themes.** (done) Embed option, project-only cache,
   download offer.
8. **subtitld.cc.** Upload, check, previews, preview page, API, app browser.
9. **Documentation.** (done) The reference, starter files and examples; checked by
   having a theme made from the document alone.
10. **Built-in themes.** A handful beyond Classic and Plain, made from the
    document, as examples of each feature.

## Notes from milestone 3

- The project's table is one field, `SUBTITLE.theme`: `{id, version,
  values: {theme id: {key: value}}, speakers: {name: {theme id: {...}}}}`,
  saved as `<subtitldcc:theme id version>` (JSON body) in the USFX
  metadata; a subtitle's own theme is `subtitldcc:theme` on it.
- A project that says nothing of a theme shows the app's
  (`CONFIG['subtitle_style']`). The first change copies what it showed into
  the project. Every change also goes to the app config, so new projects
  start with the latest choice and values.
- Not undoable yet, and not shared in co-editing: undo and co-editing
  follow docops' `META_FIELDS`, which is mirrored in the subtitld.cc repo.
  A subtitle's own theme (a segment field) is undoable and shared.

## Notes from milestone 4

- A title keeps `title_slots` ({key: text, [texts] or a picture's data: URL})
  and `title_slot_translations` ({language: {key: …}}), as JSON attributes
  in the USFX. Pictures are data: URLs up to 2 MB (what the page may load).
- A translation's export swaps in the slots' translations
  (`export_options.prepare`); the preview shows the original.
- List slots are translated as a whole list, one item a line.

## Notes from milestone 5

- Burn-in used to draw each cue once, at its start: an animated theme
  was burned frozen at its first frame. Now a cue is drawn a frame at a
  time through its intro and its outro, and once for the time between:
  **a theme holds still between its intro and its outro** (the authoring
  reference must say so). A picture file (IMSC images) gets the cue once,
  settled after its intro.
- Measured on a 30 s 1080p30 video: 6 subtitles 7 s; with 3 static titles
  about the same; with 3 Lower thirds 16.5 s. Each animated frame costs
  ~40 ms to draw and ~55 ms to composite as a full-frame PNG (frames now
  saved with lighter PNG compression, a quarter faster). An hour with 60
  lower thirds: about 3 minutes more. Worth doing later: pipe the frames to
  FFmpeg raw instead of writing PNGs.
- The timeline fades each end of an animated cue for its intro and outro,
  red when the cue is shorter than both; the editor says so too.
- Karaoke (timed words) is still burned in at the cue's start only: it
  was before this work too.

## Notes from milestone 6

- A title's export look (`title_themes.export_look`) is its theme's
  `fallback` with the title's own settings of the same names over it
  (font_family, font_size, color, box_enabled, box_color, placement,
  font_weight → bold…). Its other text slots are written as lines under
  its text. Plain and Lower third have fallbacks.
- A title whose theme file has no fallback is left out of ASS/TTML/DFXP
  (through a view: the project is untouched), and the export dialog says
  how many.
- The subtitles' "preview" look in ASS/TTML follows the project's theme:
  Classic's settings, or another theme's fallback in Classic's names with
  its own settings of those names over it.

## Notes from milestone 7

- Embedded themes are members of the USFX zip (`assets/themes/<id>.html`,
  listed in the manifest as text/html), not escaped text in the XML: the
  zip already carries assets so. A plain USF carries none.
- The option is a USFX save option beside the others (Settings → USFX:
  "Installed subtitle and title themes"), off by default. A theme only the
  project carries here is always written back, whatever the option, or a
  save would lose it; Subtitld's own are never written.
- Opening a USFX checks each carried file as any theme file
  (`themes.carry`), keeps it in the cache (`themes/project/`) while the
  project is open, never installs it. A carried theme stands in for one not
  installed, or for an older version of an installed one; never for a
  built-in.
- A project naming a theme nobody has keeps its id, version and values when
  saved (`subtitle_styles.project_table` used to write Classic's id then).
- After a project opens, a dialog lists the missing themes: the ones with a
  `cc.<hex>` id are offered from subtitld.cc (share ID from the id), the
  others are said to come from whoever sent the project. Downloads use the
  timeline themes' endpoints (`/themes/<share id>`, `/download`), so M8 must
  serve subtitle and title themes there too; installed versions are kept in
  `CONFIG['themes']['installed']`.
- Not done: offering an update when the project names a newer version than
  the installed one.

## Notes from milestone 9

- The reference is `src/subtitld/themes/AUTHORING.md`, one file (starters
  and examples inline) so it can be handed to an AI as is; it ships in the
  package beside the built-in themes (the loader reads `.html` only).
- `subtitld --check-theme FILE [--preview OUT.png]` (modules/theme_check):
  every problem, or what Subtitld reads in the file; the preview is the
  real renderer at 8 moments through intro, middle and outro, the theme
  carried, not installed. Authors had no way to see why a file was refused
  before it.
- tests/test_theme_docs.py checks every complete file in the document
  passes and draws, and that animated ones move.
- Found while writing it: an export forcing one style ("Every subtitle in
  one style") drew an animated forced theme as one still picture, because
  `subtitle_pictures.moments` asked the project's theme whether the cue is
  animated. Fixed (`themes.animation(segment, style_id)`).
- Documented limits worth revisiting: a theme without slots in its markup
  gets its text unsliced; the middle of an animated cue is one picture in
  burn-in (no loops); 32 px of room around the elements; no reload mode
  yet (copy into the themes folder and restart).

## Open questions

- Share `theme_sanitize.py` with subtitld.cc by copying, a small package, or
  a git submodule.
- How many templates the renderer keeps alive when many cues use different
  themes.
