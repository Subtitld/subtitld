"""The thumbnail of a Subtitld project, for file managers on Linux.

    subtitld-thumbnailer INPUT OUTPUT SIZE

A .usfx is a zip; Subtitld saves in it, as thumbnail.png, the picture as it
was (the video's frame with its subtitle). This copies it out for the file
manager, which scales it to the size it wants (SIZE is not needed). A file
saved before thumbnails, or without a video, has none: it exits with 1 and
the file manager shows the type's icon.

Standard library only, and nothing else of Subtitld's: file managers run
thumbnailers in a sandbox, often, and many at once.
"""
import sys
import zipfile

PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
LIMIT = 8 * 1024 * 1024        # a thumbnail; not whatever a crafted file says


def thumbnail(path):
    """thumbnail.png from the .usfx at `path`, or None."""
    try:
        with zipfile.ZipFile(path) as bundle:
            info = bundle.getinfo('thumbnail.png')
            if info.file_size > LIMIT:
                return None
            data = bundle.read(info)
    except (OSError, KeyError, zipfile.BadZipFile, RuntimeError, ValueError):
        return None
    return data if data.startswith(PNG_SIGNATURE) else None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        sys.stderr.write('usage: subtitld-thumbnailer INPUT OUTPUT [SIZE]\n')
        return 2
    data = thumbnail(argv[0])
    if data is None:
        return 1
    with open(argv[1], 'wb') as output:
        output.write(data)
    return 0


if __name__ == '__main__':
    sys.exit(main())
