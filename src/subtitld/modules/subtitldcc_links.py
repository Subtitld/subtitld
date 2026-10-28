"""subtitld:// links: subtitld.cc's "Open in Subtitld" (collab/links.py on the site).

``subtitld://<site>/project/<doc_id>`` opens a project shared for live co-editing, with no file of it here:
the subtitles come from subtitld.cc, the video and the rest from the collaborators who share them
(interface/subtitldcc_open.py). A link is only followed for the subtitld.cc this Subtitld signs in to: one
naming another site would get the person's token.

The system starts Subtitld with the link on its command line (or, on macOS, sends it to the running app);
one started while another runs hands it over (modules/link_handover.py). Nothing here loads Qt: the command
line is read before Qt may be imported (__main__.py).
"""

import os
import re
import urllib.parse

SCHEME = 'subtitld'
DOC_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')


def is_link(text):
    return isinstance(text, str) and text.lower().startswith(f'{SCHEME}:')


def parse(text):
    """{'site': host[:port], 'project': doc_id} for a link to a shared project, else None."""
    if not is_link(text):
        return None
    parts = urllib.parse.urlsplit(text.strip())
    path = [part for part in parts.path.split('/') if part]
    if not parts.netloc or len(path) != 2 or path[0] != 'project' or not DOC_ID.match(path[1]):
        return None
    return {'site': parts.netloc.lower(), 'project': path[1]}


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
