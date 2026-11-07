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
(() => {
  if (window.subtitld) return;
  const STRIP = 12;

  const css = document.createElement('style');
  css.textContent = `
    html, body { margin: 0; padding: 0; background: transparent; overflow: hidden; }
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
  `;
  document.head.prepend(css);

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
    safe.dataset.align = String(job.align);
    safe.innerHTML = '';
    if (job.html !== null) {
      const cue = document.createElement('div');
      cue.className = 'cue';
      cue.innerHTML = job.html;
      const [turnX, turnY, turnZ] = place.rotate || [0, 0, 0];
      if (turnX || turnY || turnZ) {
        // Counter-clockwise, as USF and ASS both count: the screen's y
        // axis runs the other way, so x and z are negated.
        cue.style.transform = `perspective(1000px) rotateX(${-turnX}deg) rotateY(${turnY}deg) rotateZ(${-turnZ}deg)`;
      }
      safe.append(cue);
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

    // Animations at the moment asked for, held there.
    for (const animation of document.getAnimations()) {
      animation.pause();
      animation.currentTime = job.offset;
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

  function measure(job, scale) {
    const cue = safe.querySelector('.cue');
    if (!cue) return null;
    // The cue and anything of it drawn outside it (shadows, glows, a
    // style's decorations), padded for what boxes do not count.
    let box = cue.getBoundingClientRect();
    let [left, top, right, bottom] = [box.left, box.top, box.right, box.bottom];
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
