"""subtitld:// links: subtitld.cc's "Open in Subtitld" (collab/links.py on the site).

``subtitld://<site>/project/<doc_id>`` opens a project shared for live co-editing, with no file of it here:
the subtitles come from subtitld.cc, the video and the rest from the collaborators who share them
(interface/subtitldcc_open.py). ``subtitld://<site>/subtitle/<share_id>`` (``/<version>`` for one version)
opens a subtitle published there: its USF comes from subtitld.cc, and Subtitld asks for the video. A link is only followed for the subtitld.cc this Subtitld signs in to: one
naming another site would get the person's token.

The system starts Subtitld with the link on its command line (or, on macOS, sends it to the running app);
one started while another runs hands it over (modules/link_handover.py). Nothing here loads Qt: the command
line is read before Qt may be imported (__main__.py).
"""

import os
import re
import urllib.parse

SCHEME = 'subtitld'
DOC_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')  # a project's ID, and a subtitle's share ID alike
VERSION = re.compile(r'^[1-9][0-9]{0,6}$')


def is_link(text):
    return isinstance(text, str) and text.lower().startswith(f'{SCHEME}:')


def parse(text):
    """What a link opens: {'site': host[:port], 'project': doc_id} for a shared project, {'site', 'subtitle':
    share_id, 'version': number or None} for a published subtitle; None for anything else."""
    if not is_link(text):
        return None
    parts = urllib.parse.urlsplit(text.strip())
    path = [part for part in parts.path.split('/') if part]
    if not parts.netloc or len(path) < 2 or not DOC_ID.match(path[1]):
        return None
    site = parts.netloc.lower()
    if path[0] == 'project' and len(path) == 2:
        return {'site': site, 'project': path[1]}
    if path[0] == 'subtitle' and (len(path) == 2 or (len(path) == 3 and VERSION.match(path[2]))):
        return {'site': site, 'subtitle': path[1], 'version': int(path[2]) if len(path) == 3 else None}
    return None


def for_site(link, base_url):
    """Whether `link` names the subtitld.cc at `base_url` (the one this Subtitld signs in to)."""
    return bool(link) and link['site'] == urllib.parse.urlsplit(base_url).netloc.lower()


def file_path(text):
    """A file:// URI on the command line (desktop entries pass them for files too), as a path; else `text`."""
    if isinstance(text, str) and text.lower().startswith('file://'):
        parts = urllib.parse.urlsplit(text)
        path = urllib.parse.unquote(parts.path)
        if os.name == 'nt' and re.match(r'^/[A-Za-z]:', path):
            path = path[1:]
        return path
    return text
