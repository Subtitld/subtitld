"""Waveform module

"""

import os
import subprocess
import json

from subtitld.modules.session import STARTUPINFO, FFMPEG_EXECUTABLE, PATH_TEMP, FFPROBE_EXECUTABLE


def ffmpeg_extract_subtitle(filepath, index):
    """Function to extract subtitle from video using ffmpeg"""
    command = [
        FFMPEG_EXECUTABLE,
        '-i',
        filepath,
        '-map',
        '0:' + str(index),
        os.path.join(PATH_TEMP, 'subtitle.vtt')]
    subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        startupinfo=STARTUPINFO
    ).wait()
    return os.path.join(PATH_TEMP, 'subtitle.vtt')


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


