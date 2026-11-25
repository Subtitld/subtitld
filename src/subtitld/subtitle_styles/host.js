// Runs in every subtitle style's page (subtitle_renderer injects it when the
// document is ready): puts one subtitle on the stage, at one moment of it, and
// says where it is once Chromium has drawn it.
//
// The stage is the video's picture, 1080 CSS pixels tall whatever the video's
// size (as wide as its aspect ratio makes it), scaled to the size the preview
// shows it at; a style's lengths are pixels of a 1080p picture. Inside it,
// #safe is the title-safe area, and in that the subtitle: a .cue element with
// the text as HTML. Its place in #safe follows the keypad number the text
// asks for ({\anN}), on #safe as data-align; a style may place it otherwise.
//
// Under the stage, a strip with the job's number in 16 squares: the capture
// is checked against it, as a capture taken too early still shows the job
// before. The strip draws whatever the subtitle does, so the page waits for
// the fonts the cue asks for before it says anything: until a face has
// loaded, Chromium lays the text out but draws no glyph of it, and a
// capture of that is a layer with nothing in it.
//
// A theme file (modules.themes) has markup of its own in its <body>: a
// sample for a browser, taken out here. A theme whose markup has slots
// (data-slot, "main" among them) gets that markup back for each cue, on
// the stage, its slots filled: text slots cut into .line, .word and .char
// as far as its `slice` says, image slots given their picture, list slots
// their pattern repeated. Any other gets the cue on the stage as before.
(() => {
  if (window.subtitld) return;
  const STRIP = 12;

  const css = document.createElement('style');
  css.textContent = `
    html, body { margin: 0 !important; padding: 0 !important; background: transparent !important; overflow: hidden !important; }
    .subtitld-free { position: absolute; inset: 0; }
    [data-slot] .line { display: block; }
    #subtitld-stage { position: absolute; left: 0; top: 0; height: 1080px; transform-origin: 0 0; }
    #safe { position: absolute; display: flex; flex-direction: column; }
    #safe[data-align="1"], #safe[data-align="2"], #safe[data-align="3"] { justify-content: flex-end; }
    #safe[data-align="4"], #safe[data-align="5"], #safe[data-align="6"] { justify-content: center; }
    #safe[data-align="7"], #safe[data-align="8"], #safe[data-align="9"] { justify-content: flex-start; }
    #safe[data-align="1"], #safe[data-align="4"], #safe[data-align="7"] { align-items: flex-start; text-align: left; }
    #safe[data-align="2"], #safe[data-align="5"], #safe[data-align="8"] { align-items: center; text-align: center; }
    #safe[data-align="3"], #safe[data-align="6"], #safe[data-align="9"] { align-items: flex-end; text-align: right; }
    #subtitld-stamp { position: absolute; left: 0; height: ${STRIP}px; }
    #subtitld-stamp i { position: absolute; top: 2px; width: 8px; height: 8px; background: #fff; }
    /* A run of text that flashes (modules.markup: both caption standards can
       ask for it). The rule lives here, not in a style, so every style gets
       it; the capture holds the animation at the moment it asks for, so a
       flash is on or off for certain and never half drawn. */
    @keyframes subtitld-flash { 0%, 49% { visibility: visible; } 50%, 100% { visibility: hidden; } }
    .subtitld-flash { animation: subtitld-flash 1s step-end infinite; }
    /* A subtitle timed word by word (modules.segment): each piece a span
       that lights up from --k-start for --k-length, as its effect says, in
       the style's --karaoke-color (gold where a style has none). Here too
       every style gets the rules, and the capture holds them at its moment:
       before a piece starts it is as the style draws it, after it ends it
       stays lit. All at once: the colour, from its start. Wiping across: the
       colour sweeping over it, left to right, through its length. Its
       outline: an outline in the colour, from its start. Popping in: the
       piece is not there until its start, then fades in. */
    .subtitld-k {
      --k-sung: var(--karaoke-color, #ffd700);
      animation-delay: var(--k-start);
      animation-duration: var(--k-length);
      animation-fill-mode: both;
    }
    @keyframes subtitld-k-fill { to { color: var(--k-sung); } }
    .subtitld-k-fill { animation-name: subtitld-k-fill; animation-timing-function: step-start; }
    @keyframes subtitld-k-wipe { from { background-position: 100% 0; } to { background-position: 0 0; } }
    .subtitld-k-wipe {
      animation-name: subtitld-k-wipe;
      animation-timing-function: linear;
      background-image: linear-gradient(90deg, var(--k-sung) 50%, currentColor 50%);
      background-size: 200% 100%;
      background-repeat: no-repeat;
      -webkit-background-clip: text;
      background-clip: text;
      -webkit-text-fill-color: transparent;
    }
    @keyframes subtitld-k-outline { to { -webkit-text-stroke: 0.06em var(--k-sung); } }
    .subtitld-k-outline { animation-name: subtitld-k-outline; animation-timing-function: step-start; paint-order: stroke fill; }
    @keyframes subtitld-k-pop { from { opacity: 0; } to { opacity: 1; } }
    .subtitld-k-pop { animation-name: subtitld-k-pop; animation-timing-function: ease-out; }
  `;
  document.head.prepend(css);

  // The theme's own markup, out of the page: what a slotted theme draws each cue in.
  const markup = document.createElement('template');
  for (const node of [...document.body.childNodes]) markup.content.append(node);
  const slotted = !!markup.content.querySelector('[data-slot="main"]');

  const stage = document.createElement('div');
  stage.id = 'subtitld-stage';
  const safe = document.createElement('div');
  safe.id = 'safe';
  stage.append(safe);
  const stamp = document.createElement('div');
  stamp.id = 'subtitld-stamp';
  document.body.append(stage, stamp);

  const frames = (n, then) => n ? requestAnimationFrame(() => frames(n - 1, then)) : then();
  const after = n => new Promise(done => frames(n, done));
  // A job this one takes the place of may still be waiting on a frame or a
  // font: the one asked for last is the one that answers.
  let latest = 0;

  async function render(job) {
    const mine = ++latest;
    // A view just resized: Chromium gets the new size a few frames later.
    for (let tries = 0; (innerWidth !== job.view[0] || innerHeight !== job.view[1]) && tries < 60; tries++) {
      await after(1);
      if (mine !== latest) return;
    }
    const root = document.documentElement;
    for (const name of [...root.style]) {
      if (name.startsWith('--')) root.style.removeProperty(name);
    }
    for (const [name, value] of Object.entries(job.vars)) root.style.setProperty(name, value);
    for (const name of root.getAttributeNames()) {
      if (name.startsWith('data-')) root.removeAttribute(name);
    }
    for (const [name, value] of Object.entries(job.attrs)) root.setAttribute(name, value);
    root.style.setProperty('--cue-duration', `${job.duration}ms`);
    root.style.setProperty('--cue-time', `${Math.max(0, job.offset)}ms`);
    root.classList.remove('theme-out');

    // The stage in a 1080 pixel tall picture, scaled to the view.
    const width = 1080 * job.aspect, scale = innerWidth / width;
    stage.style.width = `${width}px`;
    stage.style.transform = `scale(${scale})`;
    // Where the subtitle sits: the title-safe area, unless it carries
    // margins of its own (modules.position). A margin is measured from the
    // picture's edge, so it takes the safe area's place on that side; from
    // the middle it moves the whole band instead.
    const inset = {
      left: job.safe[0] * width, right: job.safe[0] * width,
      top: job.safe[1] * 1080, bottom: job.safe[1] * 1080,
    };
    const place = job.place || {};
    const column = (job.align - 1) % 3, row = Math.floor((job.align - 1) / 3);
    if (place.h !== null && place.h !== undefined) {
      const across = place.h / 100 * width;
      if (column === 0) inset.left = across;
      else if (column === 2) inset.right = across;
      else { inset.left += across; inset.right -= across; }
    }
    if (place.v !== null && place.v !== undefined) {
      const down = place.v / 100 * 1080;
      if (row === 0) inset.bottom = down;
      else if (row === 2) inset.top = down;
      else { inset.top += down; inset.bottom -= down; }
    }
    Object.assign(safe.style, {
      left: `${inset.left}px`, right: `${inset.right}px`,
      top: `${inset.top}px`, bottom: `${inset.bottom}px`,
    });
    // The stage and its safe area, for a theme to place itself by.
    root.style.setProperty('--stage-width', `${width}px`);
    root.style.setProperty('--stage-height', '1080px');
    root.style.setProperty('--safe-left', `${job.safe[0] * width}px`);
    root.style.setProperty('--safe-right', `${job.safe[0] * width}px`);
    root.style.setProperty('--safe-top', `${job.safe[1] * 1080}px`);
    root.style.setProperty('--safe-bottom', `${job.safe[1] * 1080}px`);
    safe.dataset.align = String(job.align);
    // Which way the writing runs. Arabic and Hebrew read right to left, and
    // a cue's base direction decides where its punctuation lands and which
    // end "start" is; the characters' own direction does the rest.
    safe.style.direction = job.direction || '';
    safe.innerHTML = '';
    for (const free of stage.querySelectorAll(':scope > .subtitld-free')) free.remove();
    if (job.html !== null) {
      const cue = document.createElement('div');
      cue.className = 'cue';
      // Where the cue's own text and its look go: the cue, or the theme's "main" slot.
      let text = cue;
      if (slotted) {
        cue.classList.add('subtitld-frame');
        cue.append(markup.content.cloneNode(true));
        fill(cue, job);
        text = cue.querySelector('[data-slot="main"]') || cue;
      } else {
        cue.innerHTML = job.html;
      }
      // What the file the subtitle came from says it should look like
      // (modules.look): inline, so it wins over the style's own rules for
      // the properties it names and leaves the style the rest.
      for (const [name, value] of Object.entries(job.look || {})) text.style.setProperty(name, value);
      // How a run of the other direction sits inside the line, where the
      // file forces it: embedded as its own run, isolated from what is
      // around it, or whatever the characters themselves say overruled.
      if (job.bidi) text.style.unicodeBidi = job.bidi === 'override' ? 'bidi-override' : job.bidi;
      // Written down the picture, in columns. Where it sits is the keypad's
      // as for any cue — a column on the right is a 6 — and #safe places it
      // there; inside, its columns line up as the row says: from the top,
      // on the middle or from the bottom.
      if (job.writing) {
        text.style.writingMode = job.writing;
        text.style.textAlign = ['end', 'center', 'start'][row];
      }
      const [turnX, turnY, turnZ] = place.rotate || [0, 0, 0];
      if (turnX || turnY || turnZ) {
        // Counter-clockwise, as USF and ASS both count: the screen's y
        // axis runs the other way, so x and z are negated.
        cue.style.transform = `perspective(1000px) rotateX(${-turnX}deg) rotateY(${turnY}deg) rotateZ(${-turnZ}deg)`;
      }
      // A theme that places itself (ignores_position) is laid over the whole
      // stage; any other where the cue's keypad number and margins say.
      if (slotted && job.free) {
        cue.classList.add('subtitld-free');
        stage.append(cue);
      } else {
        safe.append(cue);
      }
      // A piece wiping across is its text clipped out of a background, and
      // Chromium draws a text-shadow over that background: the style's
      // shadow is drawn under it instead, as a filter.
      for (const piece of cue.querySelectorAll('.subtitld-k-wipe')) {
        const shadow = getComputedStyle(piece).textShadow;
        if (shadow && shadow !== 'none') {
          piece.style.textShadow = 'none';
          piece.style.filter = shadow.split(/,(?![^(]*\))/).map(one => `drop-shadow(${one.trim()})`).join(' ');
        }
      }
    }

    // The fonts this cue asks for. A face loads when something first uses
    // it, and until it has Chromium lays the text out but draws no glyph of
    // it: the cue is a box with nothing in it, while the strip under the
    // stage draws all the same, so a capture taken now passes for this job's
    // and is kept as an empty layer. Laying out asks for the faces; then
    // they are waited for.
    void safe.offsetWidth;
    for (let tries = 0; document.fonts.status === 'loading' && tries < 120; tries++) {
      await after(1);
      if (mine !== latest) return;
    }

    // Animations at the moment asked for, held there. Those that start
    // with the outro (.theme-out, its last `outro` ms) from the outro's start.
    const intro = document.getAnimations();
    for (const animation of intro) {
      animation.pause();
      animation.currentTime = job.offset;
    }
    const outro = job.outro ? job.duration - job.outro : Infinity;
    if (job.offset >= outro) {
      root.classList.add('theme-out');
      // An intro the outro takes the place of (its element no longer names it) goes. Paused and seeked
      // by hand above, Chromium keeps it running over the outro's: one whose keyframes give an end value
      // (a `to`) would hold that value for the whole outro.
      for (const animation of intro) {
        const target = animation.effect && animation.effect.target;
        if (!animation.animationName || !target) continue;
        const names = getComputedStyle(target, animation.effect.pseudoElement || null).animationName.split(',');
        if (!names.map(name => name.trim()).includes(animation.animationName)) animation.cancel();
      }
      const before = new Set(intro);
      for (const animation of document.getAnimations()) {
        if (before.has(animation)) continue;
        animation.pause();
        animation.currentTime = job.offset - outro;
      }
    }

    stamp.style.top = `${1080 * scale}px`;
    stamp.innerHTML = '';
    for (let k = 0; k < 16; k++) {
      if (job.id & (1 << k)) {
        const square = document.createElement('i');
        square.style.left = `${4 + 10 * k}px`;
        stamp.append(square);
      }
    }

    // Where it ended up, once Chromium has had frames to draw it in. A cue
    // with text that measures to nothing has not been laid out yet: again,
    // rather than an empty rect for a subtitle there is something to draw.
    let rect = null;
    for (let tries = 0; tries < 10; tries++) {
      await after(3);
      if (mine !== latest) return;
      rect = measure(job, scale);
      if (rect !== null || !job.html) break;
    }
    console.log('SUBTITLD_RENDERED ' + JSON.stringify({id: job.id, rect, view: [innerWidth, innerHeight]}));
  }

  // A theme's slots filled for a cue: job.slots {key: html, a picture's
  // data: URL, or a list of html}, job.slot_types {key: text | image | list}.
  // An empty one is emptied and marked data-empty.
  function fill(frame, job) {
    const values = job.slots || {}, types = job.slot_types || {};
    values.main = job.html;
    for (const el of frame.querySelectorAll('[data-slot]')) {
      const key = el.dataset.slot, value = values[key], type = types[key] || 'text';
      el.removeAttribute('data-empty');
      if (type === 'image') {
        if (value) {
          if (el.tagName === 'IMG' || el.tagName === 'image') el.setAttribute(el.tagName === 'IMG' ? 'src' : 'href', value);
          else el.style.setProperty('--slot-image', `url("${value}")`);
        } else {
          el.removeAttribute('src');
          el.removeAttribute('href');
          el.style.removeProperty('--slot-image');
          el.setAttribute('data-empty', '');
        }
      } else if (type === 'list') {
        // Its first element is the pattern each item takes: the item's text
        // in its [data-item], or in it.
        const pattern = el.firstElementChild;
        el.innerHTML = '';
        const items = Array.isArray(value) ? value : [];
        if (!pattern || !items.length) {
          el.setAttribute('data-empty', '');
          continue;
        }
        items.forEach((html, i) => {
          const item = pattern.cloneNode(true);
          const target = item.querySelector('[data-item]') || item;
          target.innerHTML = html;
          item.style.setProperty('--i', i);
          slice(target, job.slice);
          el.append(item);
        });
        el.style.setProperty('--items', items.length);
      } else if (value) {
        el.innerHTML = value;
        slice(el, job.slice);
      } else {
        el.innerHTML = '';
        el.setAttribute('data-empty', '');
      }
    }
  }

  // A text slot cut into lines (at its line breaks), words and characters,
  // as far as `depth` says (none, lines, words, chars): each a span of its
  // own with its number (--i) among its kind in the slot, the slot with how
  // many there are (--lines, --words, --chars). Formatting is kept: a
  // line cut out of the middle of a <b> keeps its own <b>.
  const graphemes = typeof Intl !== 'undefined' && Intl.Segmenter ? new Intl.Segmenter(undefined, {granularity: 'grapheme'}) : null;
  function slice(el, depth) {
    if (!depth || depth === 'none') return;
    const lines = [];
    for (const br of [...el.querySelectorAll('br')]) {
      if (!el.contains(br)) continue;
      const range = document.createRange();
      range.setStart(el, 0);
      range.setEndBefore(br);
      lines.push(range.extractContents());
      br.remove();
    }
    const rest = document.createRange();
    rest.selectNodeContents(el);
    lines.push(rest.extractContents());
    el.innerHTML = '';
    let words = 0, chars = 0;
    lines.forEach((content, i) => {
      const line = document.createElement('span');
      line.className = 'line';
      line.style.setProperty('--i', i);
      line.append(content);
      el.append(line);
      if (depth === 'lines') return;
      const walker = document.createTreeWalker(line, NodeFilter.SHOW_TEXT);
      const texts = [];
      for (let node = walker.nextNode(); node; node = walker.nextNode()) texts.push(node);
      for (const node of texts) {
        const parts = node.data.split(/(\s+)/);
        const out = document.createDocumentFragment();
        for (const part of parts) {
          if (!part) continue;
          if (/^\s+$/.test(part)) { out.append(part); continue; }
          const word = document.createElement('span');
          word.className = 'word';
          word.style.setProperty('--i', words++);
          if (depth === 'chars') {
            const pieces = graphemes ? [...graphemes.segment(part)].map(g => g.segment) : [...part];
            for (const piece of pieces) {
              const char = document.createElement('span');
              char.className = 'char';
              char.style.setProperty('--i', chars++);
              char.textContent = piece;
              word.append(char);
            }
          } else {
            word.textContent = part;
          }
          out.append(word);
        }
        node.replaceWith(out);
      }
    });
    el.style.setProperty('--lines', lines.length);
    el.style.setProperty('--words', words);
    el.style.setProperty('--chars', chars);
  }

  function measure(job, scale) {
    const cue = stage.querySelector('.cue');
    if (!cue) return null;
    // The cue and anything of it drawn outside it (shadows, glows, a
    // style's decorations), padded for what boxes do not count. A theme
    // laid over the whole stage is what it draws, not the stage.
    let box = cue.getBoundingClientRect();
    let [left, top, right, bottom] = cue.classList.contains('subtitld-free')
      ? [Infinity, Infinity, -Infinity, -Infinity] : [box.left, box.top, box.right, box.bottom];
    for (const el of cue.querySelectorAll('*')) {
      const r = el.getBoundingClientRect();
      if (r.width || r.height) {
        left = Math.min(left, r.left); top = Math.min(top, r.top);
        right = Math.max(right, r.right); bottom = Math.max(bottom, r.bottom);
      }
    }
    // Nothing of it anywhere: not laid out (an empty cue has a place all the same).
    if (job.html && right <= left && bottom <= top) return null;
    const pad = job.pad * 1080 * scale, height = 1080 * scale;
    left = Math.max(0, Math.floor(left - pad)); top = Math.max(0, Math.floor(top - pad));
    right = Math.min(innerWidth, Math.ceil(right + pad)); bottom = Math.min(height, Math.ceil(bottom + pad));
    return right > left && bottom > top ? [left, top, right - left, bottom - top] : null;
  }

  window.subtitld = {render, STRIP};
  console.log('SUBTITLD_READY');
})();
