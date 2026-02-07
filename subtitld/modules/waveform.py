"""Waveform module

"""

import os
import subprocess
import json
from PySide6.QtCore import QPointF, QThread, Signal, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF
import numpy
import numpy as np
import ffms2
import asyncio

from subtitld.modules.session import STARTUPINFO, FFMPEG_EXECUTABLE, path_tmp, FFPROBE_EXECUTABLE


def ffms2_load_audio(filepath, samplerate=48000, mono=True, normalize=True,
                     in_type=np.int16, out_type=np.float32, chunk_size=4096*64):
    asource = ffms2.AudioSource(filepath)
    total_samples = asource.properties.NumSamples
    channels = asource.properties.Channels

    # Initialize a buffer large enough for one chunk
    asource.init_buffer(chunk_size)

    chunks = []
    start = 0

    while start < total_samples:
        # Determine the effective chunk size for this iteration
        remaining = total_samples - start
        if remaining < chunk_size:
            asource.init_buffer(remaining)

        try:
            chunk = asource.get_audio(start)
        except ffms2.Error as e:
            break

        chunks.append(chunk)
        start += chunk_size

    sound_np = np.concatenate(chunks, axis=0)

    # Convert and normalize if requested
    sound_np = sound_np.astype(out_type)
    if normalize:
        if np.issubdtype(in_type, np.integer):
            sound_np /= np.iinfo(in_type).max

    # Convert to mono if needed
    if mono and channels > 1:
        sound_np = np.mean(sound_np, axis=1)
    return sound_np


def ffmpeg_extract_subtitle(filepath, index):
    """Function to extract subtitle from video using ffmpeg"""
    command = [
        FFMPEG_EXECUTABLE,
        '-i',
        filepath,
        '-map',
        '0:' + str(index),
        os.path.join(path_tmp, 'subtitle.vtt')]
    subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, startupinfo=STARTUPINFO).wait()
    return os.path.join(path_tmp, 'subtitle.vtt')


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
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, startupinfo=STARTUPINFO)
    json_file = False
    with proc.stdout as stdout:
        json_file = json.loads(stdout.read())

    return json_file


