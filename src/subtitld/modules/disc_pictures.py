"""Subtitles drawn in true colour, made ready for a disc's picture formats.

The renderer (interface.subtitle_pictures) draws each subtitle on its own,
in true colour, its edges soft. A DVD (vobsub) or a Blu-ray (pgs) shows one
picture at a time, in few colours:

- `timeline` turns the subtitles into what is on screen from one moment to
  the next: where two overlap (a sign at the top over a line at the
  bottom), the time they share is one picture with both drawn on it, and
  each is shown alone for the time it is alone;
- `fit` cuts a picture to the frame, outside of which a disc places nothing;
- `reduce` cuts its colours down to the few allowed: a DVD's subpicture has
  four, one of them the transparent background, and a Blu-ray's object up
  to 256, each colour with an opacity of its own. Index 0 is always the
  transparent one.

A picture with no more colours than are allowed keeps them exactly.
Otherwise they are found by k-means, in premultiplied space (each colour
times its opacity, beside the opacity): a pixel that barely shows is near
transparent whatever its colour, so the soft edge of a black outline over
nothing comes out as that black half seen, not as grey. The first guesses
are the colours that cover most of the picture and stand farthest from
those already chosen, which finds the fill and the outline before the
shades between them. `cluster` is the same k-means, for any points:
vobsub gathers the colours of every subtitle into its file's palette of
sixteen with it.

A picture here is a dict: 'start', 'end' (seconds), 'x', 'y', 'width',
'height' (pixels on the frame), 'rgba' (width x height pixels, four bytes
each, alpha not premultiplied) and 'forced'.
"""

ITERATIONS = 12              # rounds of k-means after the first guesses


def timeline(pictures):
    """What is on screen, picture by picture, for `pictures` in order of
    start: the same dicts, their end brought forward to where another
    starts, and a picture of both (or more) for the time they share,
    forced when any of them is. Only the pictures on screen are held."""
    pictures = iter(pictures)
    upcoming = next(pictures, None)
    shown = []
    now = upcoming['start'] if upcoming else 0.0
    while shown or upcoming:
        while upcoming and upcoming['start'] <= now:
            if upcoming['end'] > upcoming['start']:
                shown.append(upcoming)
            upcoming = next(pictures, None)
        shown = [picture for picture in shown if picture['end'] > now]
        if not shown:
            if upcoming is None:
                break
            now = upcoming['start']
            continue
        until = min([picture['end'] for picture in shown] + ([upcoming['start']] if upcoming else []))
        yield dict(_composite(shown), start=now, end=until)
        now = until


def _composite(pictures):
    """One picture of `pictures` laid on each other, the later on top."""
    if len(pictures) == 1:
        return dict(pictures[0])
    import numpy
    left = min(p['x'] for p in pictures)
    top = min(p['y'] for p in pictures)
    right = max(p['x'] + p['width'] for p in pictures)
    bottom = max(p['y'] + p['height'] for p in pictures)
    canvas = numpy.zeros((bottom - top, right - left, 4), numpy.float64)      # premultiplied, 0 to 1
    for picture in pictures:
        pixels = numpy.frombuffer(picture['rgba'], numpy.uint8).reshape(picture['height'], picture['width'], 4) / 255
        alpha = pixels[..., 3:4]
        layer = numpy.concatenate([pixels[..., :3] * alpha, alpha], axis=2)
        area = canvas[picture['y'] - top:picture['y'] - top + picture['height'],
                      picture['x'] - left:picture['x'] - left + picture['width']]
        area[...] = layer + area * (1 - alpha)
    alpha = canvas[..., 3:4]
    colour = numpy.divide(canvas[..., :3], alpha, out=numpy.zeros_like(canvas[..., :3]), where=alpha > 0)
    rgba = numpy.rint(numpy.concatenate([colour, alpha], axis=2) * 255).clip(0, 255).astype(numpy.uint8)
    return {'x': left, 'y': top, 'width': right - left, 'height': bottom - top, 'rgba': rgba.tobytes(),
            'forced': any(p.get('forced') for p in pictures)}


def fit(picture, size):
    """A picture cut to the frame `size` it is placed on: (x, y, width,
    height, rgba), or None when none of it is on the frame."""
    import numpy
    x, y, width, height = (int(picture[key]) for key in ('x', 'y', 'width', 'height'))
    left, top = max(0, x), max(0, y)
    right, bottom = min(int(size[0]), x + width), min(int(size[1]), y + height)
    if right <= left or bottom <= top:
        return None
    rgba = picture['rgba']
    if (left, top, right, bottom) != (x, y, x + width, y + height):
        pixels = numpy.frombuffer(rgba, numpy.uint8).reshape(height, width, 4)
        rgba = pixels[top - y:bottom - y, left - x:right - x].tobytes()
    return left, top, right - left, bottom - top, rgba


def cluster(points, weights, count, pinned=None):
    """At most `count` centres for `points` (an N x D array) of the given
    weights, and the centre each point is nearest: (centres, labels). A
    `pinned` centre (transparent, for a picture) comes first and stays
    where it is, and `count` does not count it."""
    import numpy
    points = numpy.asarray(points, numpy.float64)
    weights = numpy.asarray(weights, numpy.float64)
    if pinned is not None:
        centres = [numpy.asarray(pinned, numpy.float64)]
    else:
        centres = [points[int(numpy.argmax(weights))]]
    nearest = ((points - centres[0]) ** 2).sum(1)
    wanted = count + (pinned is not None)
    # First guesses: each the point that most weight would be far from.
    while len(centres) < wanted:
        score = weights * nearest
        chosen = int(numpy.argmax(score))
        if score[chosen] <= 0:
            break
        centres.append(points[chosen])
        nearest = numpy.minimum(nearest, ((points - points[chosen]) ** 2).sum(1))
    centres = numpy.array(centres)
    fixed = 1 if pinned is not None else 0
    labels = _nearest(points, centres)
    for _ in range(ITERATIONS):
        totals = numpy.bincount(labels, weights, len(centres))
        used = totals > 0
        moved = centres.copy()
        for axis in range(points.shape[1]):
            sums = numpy.bincount(labels, weights * points[:, axis], len(centres))
            moved[used, axis] = sums[used] / totals[used]
        moved[:fixed] = centres[:fixed]
        if numpy.allclose(moved, centres):
            break
        centres = moved
        labels = _nearest(points, centres)
    return centres, labels


def _nearest(points, centres):
    import numpy
    distances = (points ** 2).sum(1)[:, None] - 2 * points @ centres.T + (centres ** 2).sum(1)[None, :]
    return numpy.argmin(distances, axis=1)


def reduce(rgba, count):
    """(indices, colours) for a picture's pixels, `rgba` four bytes a
    pixel: a byte a pixel, and at most `count` colours [(r, g, b, a)], the
    first (0, 0, 0, 0)."""
    import numpy
    pixels = numpy.frombuffer(rgba, numpy.uint8).reshape(-1, 4)
    packed = numpy.ascontiguousarray(pixels).view('<u4').ravel()
    unique, inverse, counts = numpy.unique(packed, return_inverse=True, return_counts=True)
    inverse = inverse.ravel()
    colours = unique.view(numpy.uint8).reshape(-1, 4)
    shown = colours[:, 3] > 0
    lookup = numpy.zeros(len(unique), numpy.uint8)
    if int(shown.sum()) < count:
        # Few enough to keep as they are.
        lookup[shown] = numpy.arange(1, int(shown.sum()) + 1)
        palette = [(0, 0, 0, 0)] + [tuple(int(v) for v in colour) for colour in colours[shown]]
        return lookup[inverse].tobytes(), palette
    alpha = colours[:, 3:4].astype(numpy.float64)
    points = numpy.hstack([colours[:, :3] * alpha / 255, alpha])
    centres, labels = cluster(points[shown], counts[shown], count - 1, pinned=(0, 0, 0, 0))
    # The centres back to straight colours; one that rounds to nothing
    # seen is the transparent one, and those no pixel took are left out.
    palette = [(0, 0, 0, 0)]
    renumber = numpy.zeros(len(centres), numpy.uint8)
    for number, centre in enumerate(centres[1:], start=1):
        a = int(round(centre[3]))
        if a <= 0 or not (labels == number).any():
            continue
        renumber[number] = len(palette)
        palette.append(tuple(max(0, min(255, int(round(v * 255 / centre[3])))) for v in centre[:3]) + (min(255, a),))
    lookup[shown] = renumber[labels]
    return lookup[inverse].tobytes(), palette
