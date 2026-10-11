"""Waveform module

"""

import subprocess
import json

from subtitld.modules.session import STARTUPINFO, FFPROBE_EXECUTABLE


def ffmpeg_load_metadata(filepath):
    """Function to read video's metadata"""
    command = [
        FFPROBE_EXECUTABLE,
        '-v',
        'quiet',
        '-print_format',
        'json',
        '-show_format',
        '-show_streams',
        filepath]
    proc = subprocess.Popen(
        command, 
        stdout=subprocess.PIPE, 
        stderr=subprocess.DEVNULL, 
        startupinfo=STARTUPINFO
    )
    json_file = False
    with proc.stdout as stdout:
        json_file = json.loads(stdout.read())

    return json_file


