import os
import copy
import hashlib
import shutil
import zipfile
from docx import Document
import json
import pycaption
from pycaption.exceptions import CaptionReadSyntaxError, CaptionReadNoCaptions
import chardet
import pysubs2
import datetime
from bs4 import BeautifulSoup

from PySide6.QtWidgets import QFileDialog
from PySide6.QtCore import Qt, QThread, Signal, QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImage

from subtitld.modules import timecode
from subtitld.modules import session
from subtitld.modules import waveform
from subtitld.modules import usf
from subtitld.modules import utils


def _safe_asset_name(name):
    safe = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in str(name))
    return safe or 'unnamed'


def _usfx_extract_dir(usfx_path):
    project_hash = hashlib.md5(os.path.abspath(usfx_path).encode('utf-8')).hexdigest()
    path = os.path.join(session.PATH_SUBTITLD_USER_CACHE, 'usfx', project_hash)
    os.makedirs(path, exist_ok=True)
    return path


USFX_OPTION_DEFAULTS = {
    'include_speaker_images': True,
    'include_waveform_cache': False,
    'include_original_audio': False,
    'include_processed_audio': False,
    'include_original_video': False,
}


def _get_usfx_options():
    """Merge stored defaults with fallback values."""
    stored = {}
    if isinstance(session.CONFIG, dict):
        stored = session.CONFIG.get('default_values', {}).get('usfx_options') or {}
    return {**USFX_OPTION_DEFAULTS, **stored}

from subtitld.interface.translation import _


class ThreadGenerateHashOfVideo(QThread):
    """Thread to extract time positions of scenes"""
    response = Signal(list)
    filepath = ''

    def run(self):
        if self.filepath and os.path.isfile(self.filepath):
            md5_object = hashlib.md5()
            block_size = 128 * md5_object.block_size
            file_object = open(self.filepath, 'rb')
            chunk = file_object.read(block_size)
            while chunk:
                md5_object.update(chunk)
                chunk = file_object.read(block_size)

            md5_hash = md5_object.hexdigest()

            self.response.emit([self.filepath, md5_hash])


def process_subtitles_file(subtitle_file=False, subtitle_format='SRT'):
    """Definition to process subtitle file. It returns a dict with the subtitles."""
    segments_list = []

    if subtitle_file and os.path.isfile(subtitle_file):
        if subtitle_file.lower().endswith(('.srt')):
            enc = chardet.detect(open(subtitle_file, 'rb').read())['encoding']
            with open(subtitle_file, mode='rb') as srt_file:
                srt_content = srt_file.read().decode(enc, 'ignore')

                if ' -> ' in srt_content:
                    srt_content = srt_content.replace(' -> ', ' --> ')

                srt_reader = pycaption.SRTReader().read(srt_content)
                languages = srt_reader.get_languages()
                language = languages[0]
                captions = srt_reader.get_captions(language)
                for caption in captions:
                    segments_list.append({
                        'start': caption.start / 1000000,
                        'end': caption.end / 1000000,
                        'text': caption.get_text()
                    })

        elif subtitle_file.lower().endswith(('.vtt', '.webvtt')):
            subtitle_format = 'VTT'
            with open(subtitle_file, encoding='utf-8') as vtt_file:
                try:
                    vtt_reader = pycaption.WebVTTReader().read(vtt_file.read())
                    languages = vtt_reader.get_languages()
                    language = languages[0]
                    captions = vtt_reader.get_captions(language)
                    for caption in captions:
                        segments_list.append({
                            'start': caption.start / 1000000,
                            'end': caption.end / 1000000,
                            'text': caption.get_text()
                        })
                except CaptionReadSyntaxError:
                    with open(subtitle_file, encoding='utf-8') as fileobj:
                        subfile = pysubs2.SSAFile.from_string(fileobj.read())

                    for event in subfile.events:
                        segments_list.append({
                            'start': event.start / 1000.0,
                            'end': (event.start / 1000.0) + (event.duration / 1000.0),
                            'text': event.text
                        })
                except CaptionReadNoCaptions:
                    pass

                    # error_message = QMessageBox()
                    # error_message.setWindowTitle('There is a problem with this file and can not be opened.')

        elif subtitle_file.lower().endswith(('.ttml', '.dfxp', '.xml', '.itt')):
            if '<tt ' in open(subtitle_file).read():
                subtitle_format = 'TTML'
            else:
                subtitle_format = 'DFXP'

            with open(subtitle_file, encoding='utf-8') as dfxp_file:
                dfxp_reader = pycaption.DFXPReader().read(dfxp_file.read())
                languages = dfxp_reader.get_languages()
                language = languages[0]
                captions = dfxp_reader.get_captions(language)
                for caption in captions:
                    segments_list.append({
                        'start': caption.start / 1000000,
                        'end': caption.end / 1000000,
                        'text': caption.get_text()
                    })

        elif subtitle_file.lower().endswith(('.smi', '.sami')):
            subtitle_format = 'SAMI'
            with open(subtitle_file, encoding='utf-8') as sami_file:
                sami_reader = pycaption.SAMIReader().read(sami_file.read())
                languages = sami_reader.get_languages()
                language = languages[0]
                captions = sami_reader.get_captions(language)
                for caption in captions:
                    segments_list.append({
                        'start': caption.start / 1000000,
                        'end': caption.end / 1000000,
                        'text': caption.get_text()
                    })

        # elif subtitle_file.lower().endswith(('.sbv')):
        #     subtitle_format = 'SBV'
        #     with open(subtitle_file, encoding='utf-8') as sbv_file:
        #         from captionstransformer.sbv import Reader
        #         captions = Reader(sbv_file).read()
        #         for caption in captions:
        #             segments_list.append([(caption.start - datetime.datetime(1900, 1, 1)).total_seconds(), caption.duration.total_seconds(), caption.text.strip()])

        elif subtitle_file.lower().endswith(('.ass', '.ssa', '.sub')):
            if subtitle_file.lower().endswith(('.ass', '.ssa')):
                subtitle_format = 'ASS'
                with open(subtitle_file, encoding='utf-8') as fileobj:
                    subfile = pysubs2.SSAFile.from_string(fileobj.read())
            elif subtitle_file.lower().endswith(('.sub')):
                subtitle_format = 'SUB'
                enc = chardet.detect(open(subtitle_file, 'rb').read())['encoding']
                subfile = pysubs2.SSAFile.from_string(open(subtitle_file, mode='rb').read().decode(enc, 'ignore'))

            for event in subfile.events:
                segments_list.append({
                    'start': event.start / 1000.0,
                    'end': (event.start / 1000.0) + (event.duration / 1000.0),
                    'text': event.plaintext
                })

        elif subtitle_file.lower().endswith(('.scc')):
            subtitle_format = 'SCC'

            with open(subtitle_file, encoding='utf-8') as scc_file:
                scc_reader = pycaption.SCCReader().read(scc_file.read())
                languages = scc_reader.get_languages()
                language = languages[0]
                captions = scc_reader.get_captions(language)
                for caption in captions:
                    segments_list.append({
                        'start': caption.start / 1000000,
                        'end': caption.end / 1000000,
                        'text': caption.get_text()
                    })

        elif subtitle_file.lower().endswith(('.usf')):
            subtitle_format = 'USF'
            reader = usf.USFReader()
            segments_list = reader.read(open(subtitle_file).read())
            for speaker_name, speaker_data in reader.speakers.items():
                existing = session.SPEAKERS.get(speaker_name, {})
                if 'color' in speaker_data:
                    existing['color'] = speaker_data['color']
                if isinstance(speaker_data.get('dubbing'), dict):
                    existing['dubbing'] = speaker_data['dubbing']
                image_bytes = speaker_data.get('image_bytes')
                if image_bytes:
                    qimg = QImage()
                    if qimg.loadFromData(image_bytes):
                        existing['image'] = qimg
                session.SPEAKERS[speaker_name] = existing

            if not isinstance(session.FORMAT, dict):
                session.FORMAT = {}
            session.FORMAT['format'] = 'USF'
            session.FORMAT['options'] = {
                'embed_speaker_images': reader.has_embedded_images,
                'embed_audio_clips': reader.has_embedded_audio_clips,
            }

            dub_cache_dir = os.path.join(session.PATH_SUBTITLD_USER_CACHE, 'waveform')
            os.makedirs(dub_cache_dir, exist_ok=True)
            for segment in segments_list:
                for dub in segment.get('dubbing', []) or []:
                    raw = dub.pop('_embedded_bytes', None)
                    fmt = dub.pop('_embedded_format', 'wav')
                    if raw and dub.get('uid'):
                        target = os.path.join(dub_cache_dir, f'{dub["uid"]}.{fmt}')
                        if not os.path.exists(target):
                            try:
                                with open(target, 'wb') as wf:
                                    wf.write(raw)
                            except Exception:
                                pass
                        dub['path'] = target

        elif subtitle_file.lower().endswith(('.usfx')):
            subtitle_format = 'USFX'
            extract_dir = _usfx_extract_dir(subtitle_file)

            with zipfile.ZipFile(subtitle_file, 'r') as zf:
                zf.extractall(extract_dir)

            inner_usf = os.path.join(extract_dir, 'subtitles.usf')
            if not os.path.exists(inner_usf):
                for fname in os.listdir(extract_dir):
                    if fname.lower().endswith('.usf'):
                        inner_usf = os.path.join(extract_dir, fname)
                        break

            reader = usf.USFReader()
            with open(inner_usf, encoding='utf-8') as fh:
                segments_list = reader.read(fh.read())

            speakers_dir = os.path.join(extract_dir, 'assets', 'speakers')
            for speaker_name, speaker_data in reader.speakers.items():
                existing = session.SPEAKERS.get(speaker_name, {})
                if 'color' in speaker_data:
                    existing['color'] = speaker_data['color']
                if isinstance(speaker_data.get('dubbing'), dict):
                    existing['dubbing'] = speaker_data['dubbing']
                image_bytes = speaker_data.get('image_bytes')
                if image_bytes:
                    qimg = QImage()
                    if qimg.loadFromData(image_bytes):
                        existing['image'] = qimg
                else:
                    safe_name = _safe_asset_name(speaker_name)
                    for ext in ('png', 'jpg', 'jpeg'):
                        candidate = os.path.join(speakers_dir, f'{safe_name}.{ext}')
                        if os.path.exists(candidate):
                            qimg = QImage()
                            if qimg.load(candidate):
                                existing['image'] = qimg
                            break
                session.SPEAKERS[speaker_name] = existing

            for segment in segments_list:
                for dub in segment.get('dubbing', []) or []:
                    path = dub.get('path', '')
                    if path and not os.path.isabs(path):
                        dub['path'] = os.path.join(extract_dir, path)

            # Resolve the project's source video. Priority: bundled video file →
            # original-path entry recorded in the manifest → same-folder match
            # against the manifest basename. Fall back is leaving session.VIDEO
            # empty so the start screen prompts the user.
            if not isinstance(session.VIDEO, dict):
                session.VIDEO = {}

            bundled_video_dir = os.path.join(extract_dir, 'assets', 'video')
            if os.path.isdir(bundled_video_dir):
                for fname in sorted(os.listdir(bundled_video_dir)):
                    candidate = os.path.join(bundled_video_dir, fname)
                    if os.path.isfile(candidate):
                        session.VIDEO['filepath'] = candidate
                        break

            manifest_path = os.path.join(extract_dir, 'manifest.xml')
            manifest_soup = None
            if os.path.isfile(manifest_path):
                try:
                    manifest_soup = BeautifulSoup(open(manifest_path, encoding='utf-8').read(), 'lxml-xml')
                except Exception:
                    manifest_soup = None

            if not session.VIDEO.get('filepath') and manifest_soup is not None:
                source_tag = manifest_soup.find('source')
                if source_tag is not None:
                    recorded_path = source_tag.get('path', '') or ''
                    recorded_basename = source_tag.get('basename', '') or os.path.basename(recorded_path)
                    usfx_dir = os.path.dirname(os.path.abspath(subtitle_file))
                    for candidate in (recorded_path, os.path.join(usfx_dir, recorded_basename) if recorded_basename else ''):
                        if candidate and os.path.isfile(candidate):
                            session.VIDEO['filepath'] = candidate
                            break

            if manifest_soup is not None:
                voicemix_tag = manifest_soup.find('voicemix')
                if voicemix_tag is not None and voicemix_tag.get('volume') is not None:
                    try:
                        session.VIDEO['music_voice_separation_volume_pending'] = float(voicemix_tag.get('volume'))
                    except (TypeError, ValueError):
                        pass

            # Copy bundled caches to the cache directories, keyed against the current video.
            current_video = session.VIDEO.get('filepath') if isinstance(session.VIDEO, dict) else None
            current_key = utils.get_cache_key(current_video) if current_video else None
            if current_key:
                wf_bundled = os.path.join(extract_dir, 'assets', 'waveform.npy')
                if os.path.isfile(wf_bundled):
                    wf_dir = os.path.join(session.PATH_SUBTITLD_USER_CACHE, 'waveform')
                    os.makedirs(wf_dir, exist_ok=True)
                    wf_target = os.path.join(wf_dir, f'{current_key}_waveform.npy')
                    if not os.path.exists(wf_target):
                        try:
                            shutil.copy(wf_bundled, wf_target)
                        except Exception:
                            pass

                for kind in ('original', 'vocals', 'background'):
                    bundled = os.path.join(extract_dir, 'assets', 'audio', f'{kind}.flac')
                    if os.path.isfile(bundled):
                        target = os.path.join(session.PATH_SUBTITLD_DATA_AUDIOSEPARATION, f'{current_key}_{kind}.flac')
                        if not os.path.exists(target):
                            try:
                                shutil.copy(bundled, target)
                            except Exception:
                                pass

            if not isinstance(session.FORMAT, dict):
                session.FORMAT = {}
            session.FORMAT['format'] = 'USFX'
            session.FORMAT['options'] = {}

        elif subtitle_file.lower().endswith(('.json')):
            subtitle_format = 'JSON'

            with open(subtitle_file, encoding='utf-8') as json_file:
                source_content = json.load(json_file)

                for segment in source_content['segments']:
                    segments_list.append({
                        'start': segment['start'],
                        'end': segment['end'],
                        'text': segment['text'],
                        'speaker': segment.get('speaker', 'A'),
                        'translations': segment.get('translations', {})
                    })
            if not 'config' in session.FORMAT:
                session.FORMAT['config'] = {
                    'standard': 'Whisper'
                }

    if not 'format' in session.FORMAT:
        session.FORMAT['format'] = subtitle_format

    return segments_list, subtitle_format


def process_video_file(video_file=False):
    """Function to process video file. It returns a dict with video properties."""
    video_metadata = {}
    json_result = waveform.ffmpeg_load_metadata(video_file)
    video_metadata['audio'] = False
    video_metadata['audio_is_present'] = False
    video_metadata['waveform'] = {}
    video_metadata['duration'] = float(json_result.get('format', {}).get('duration', '0.01'))
    for stream in json_result.get('streams', []):
        if stream.get('codec_type', '') == 'video' and not stream.get('codec_name', 'png') in ['png', 'mjpeg']:
            video_metadata['width'] = int(stream.get('width', 640))
            video_metadata['height'] = int(stream.get('height', 480))
            video_metadata['framerate'] = int(stream.get('r_frame_rate', '1/30').split('/', 1)[0]) / int(stream.get('r_frame_rate', '1/30').split('/', 1)[-1])
        elif stream.get('codec_type', '') in ['subtitle'] and not video_metadata.get('subtitles', False):
            video_metadata['subtitles'] = waveform.ffmpeg_extract_subtitle(video_file, stream.get('index', 2))
            # TODO: select what language if multiple embedded subtitles
        elif stream.get('codec_type', '') in ['audio']:
            video_metadata['audio_is_present'] = True
    video_metadata['filepath'] = video_file
    video_metadata['scenes'] = []

    return video_metadata


def import_file(filename=False, subtitle_format=False):  # , fit_to_length=False, length=.01, distribute_fixed_duration=False):
    """Function to import file into the subtitle project."""
    session.SUBTITLE['segments'] = []
    if filename:
        if filename.lower().endswith(('.txt')):
            subtitle_format = 'TXT'
            with open(filename) as txt_file:
                # clean("some input",
                #     fix_unicode=True,               # fix various unicode errors
                #     to_ascii=True,                  # transliterate to closest ASCII representation
                #     lower=True,                     # lowercase text
                #     no_line_breaks=False,           # fully strip line breaks as opposed to only normalizing them
                #     no_urls=False,                  # replace all URLs with a special token
                #     no_emails=False,                # replace all email addresses with a special token
                #     no_phone_numbers=False,         # replace all phone numbers with a special token
                #     no_numbers=False,               # replace all numbers with a special token
                #     no_digits=False,                # replace all digits with a special token
                #     no_currency_symbols=False,      # replace all currency symbols with a special token
                #     no_punct=False,                 # fully remove punctuation
                #     replace_with_url="<URL>",
                #     replace_with_email="<EMAIL>",
                #     replace_with_phone_number="<PHONE>",
                #     replace_with_number="<NUMBER>",
                # replace_with_digit="0",
                # replace_with_currency_symbol="<CUR>",
                # lang="en"                       # set to 'de' for German special handling
                # )

                # txt_content = clean(txt_file.read())
                txt_content = txt_file.read()
                pos = 0.0
                for phrase in txt_content.split('. '):
                    session.SUBTITLE['segments'].append({
                        'start': pos,
                        'end': pos + 5.0, 
                        'text': phrase + '.'
                    })
                    pos += 5.0

        elif filename.lower().endswith(('.srt')):
            subtitle_format = 'SRT'
            session.SUBTITLE['segments'] += process_subtitles_file(subtitle_file=filename, subtitle_format=subtitle_format)[0]

        elif filename.lower().endswith(('.docx')):
            subtitle_format = 'DOCX'
            txt_content = ''

            doc = Document(filename)
            for par in doc.paragraphs:
                txt_content += par.text

            pos = 0.0
            for phrase in txt_content.split('. '):
                session.SUBTITLE['segments'].append({
                    'start': pos,
                    'end': pos + 5.0,
                    'text': phrase + '.'
                })
                pos += 5.0

            session.SUBTITLE['segments'] += process_subtitles_file(subtitle_file=filename, subtitle_format=subtitle_format)[0]

    return session.SUBTITLE['segments'], subtitle_format


def export_file(filename=False, export_format='TXT', options=False):
    """Function to export file. A filepath and a subtitle dict is given."""
    if session.SUBTITLE['segments'] and filename:
        if export_format in ['.txt']:
            final_txt = ''
            for sub in session.SUBTITLE['segments']:
                final_txt += sub[2].replace('\n', ' ') + ' '
            if options:
                if options.get('new_line', False):
                    final_txt = final_txt.replace('. ', '.\n')
                    final_txt = final_txt.replace('! ', '!\n')
                    final_txt = final_txt.replace('? ', '?\n')
            with open(filename, mode='w', encoding='utf-8') as txt_file:
                txt_file.write(final_txt)
        elif export_format in ['.kdenlive']:
            final_xml = '''<?xml version='1.0' encoding='utf-8'?><mlt LC_NUMERIC="C" producer="main_bin" version="6.26.1" root="/home/jonata"><profile frame_rate_num="25" sample_aspect_num="1" display_aspect_den="9" colorspace="709" progressive="1" description="HD 1080p 25 fps" display_aspect_num="16" frame_rate_den="1" width="1920" height="1080" sample_aspect_den="1"/>'''

            i = 0
            for sub in session.SUBTITLE['segments']:
                final_xml += '''<producer id="producer{i}" in="{zerotime}" out="{out}">
                                <property name="length">{length}</property>
                                <property name="eof">pause</property>
                                <property name="resource"/>
                                <property name="progressive">1</property>
                                <property name="aspect_ratio">1</property>
                                <property name="seekable">1</property>
                                <property name="mlt_service">kdenlivetitle</property>
                                <property name="kdenlive:duration">125</property>
                                <property name="kdenlive:clipname">{clipname}</property>
                                <property name="xmldata">&lt;kdenlivetitle duration="125" LC_NUMERIC="C" width="1920" height="1080" out="124"> &lt;item type="QGraphicsTextItem" z-index="0"> &lt;position x="784" y="910"> &lt;transform>1,0,0,0,1,0,0,0,1&lt;/transform> &lt;/position>
                                &lt;content shadow="0;#64000000;3;3;3" font-underline="0" box-height="62" font-outline-color="0,0,0,255" font="Ubuntu" letter-spacing="0" font-pixel-size="54" font-italic="0" typewriter="0;2;1;0;0" alignment="1" font-weight="50" font-outline="0"
                                box-width="351.719" font-color="255,255,255,255">{content}&lt;/content> &lt;/item> &lt;startviewport rect="0,0,1920,1080"/> &lt;endviewport rect="0,0,1920,1080"/> &lt;background color="0,0,0,0"/> &lt;/kdenlivetitle>
                                </property>
                                <property name="kdenlive:folderid">-1</property>
                                <property name="kdenlive:id">{id}</property>
                                <property name="force_reload">0</property>
                                <property name="meta.media.width">1920</property>
                                <property name="meta.media.height">1080</property>
                            </producer>'''.format(i=i, id=i + 2, length=int(sub[1] * 25), clipname='Subtitle {i}'.format(i=i), content=sub[2], zerotime=str(timecode.Timecode('1000', start_seconds=0.001, fractional=True)), out=str(timecode.Timecode('1000', start_seconds=sub[1], fractional=True)))
                i += 1

            final_xml += '''<playlist id="main_bin">
                            <property name="kdenlive:docproperties.activeTrack">2</property>
                            <property name="kdenlive:docproperties.audioChannels">2</property>
                            <property name="kdenlive:docproperties.audioTarget">-1</property>
                            <property name="kdenlive:docproperties.disablepreview">0</property>
                            <property name="kdenlive:docproperties.documentid">1621801540856</property>
                            <property name="kdenlive:docproperties.enableTimelineZone">0</property>
                            <property name="kdenlive:docproperties.enableexternalproxy">0</property>
                            <property name="kdenlive:docproperties.enableproxy">0</property>
                            <property name="kdenlive:docproperties.externalproxyparams">../Sub;;S03.MP4;../Clip;;.MXF</property>
                            <property name="kdenlive:docproperties.generateimageproxy">0</property>
                            <property name="kdenlive:docproperties.generateproxy">0</property>
                            <property name="kdenlive:docproperties.groups">[ ]
                            </property>
                            <property name="kdenlive:docproperties.kdenliveversion">21.04.0</property>
                            <property name="kdenlive:docproperties.position">372</property>
                            <property name="kdenlive:docproperties.previewextension"/>
                            <property name="kdenlive:docproperties.previewparameters"/>
                            <property name="kdenlive:docproperties.profile">atsc_1080p_25</property>
                            <property name="kdenlive:docproperties.proxyextension"/>
                            <property name="kdenlive:docproperties.proxyimageminsize">2000</property>
                            <property name="kdenlive:docproperties.proxyimagesize">800</property>
                            <property name="kdenlive:docproperties.proxyminsize">1000</property>
                            <property name="kdenlive:docproperties.proxyparams"/>
                            <property name="kdenlive:docproperties.scrollPos">0</property>
                            <property name="kdenlive:docproperties.seekOffset">30000</property>
                            <property name="kdenlive:docproperties.version">1</property>
                            <property name="kdenlive:docproperties.verticalzoom">1</property>
                            <property name="kdenlive:docproperties.videoTarget">-1</property>
                            <property name="kdenlive:docproperties.zonein">0</property>
                            <property name="kdenlive:docproperties.zoneout">75</property>
                            <property name="kdenlive:docproperties.zoom">8</property>
                            <property name="kdenlive:expandedFolders"/>
                            <property name="kdenlive:documentnotes"/>
                            <property name="xml_retain">1</property>\n'''

            i = 0
            for sub in session.SUBTITLE['segments']:
                final_xml += '''<entry producer="producer{i}" in="{zerotime}" out="{out}"/>\n'''.format(i=i, zerotime=str(timecode.Timecode('1000', start_seconds=0.001, fractional=True)), out=str(timecode.Timecode('1000', start_seconds=sub[1], fractional=True)))
                i += 1

            final_xml += '''</playlist>
                            <producer id="black_track" in="00:00:00.000" out="00:20:12.120">
                                <property name="length">2147483647</property>
                                <property name="eof">continue</property>
                                <property name="resource">black</property>
                                <property name="aspect_ratio">1</property>
                                <property name="mlt_service">color</property>
                                <property name="mlt_image_format">rgb24a</property>
                                <property name="set.test_audio">0</property>
                            </producer>
                            <playlist id="playlist0">
                                <property name="kdenlive:audio_track">1</property>
                            </playlist>
                            <playlist id="playlist1"/>
                            <tractor id="tractor0" in="00:00:00.000">
                                <property name="kdenlive:audio_track">1</property>
                                <property name="kdenlive:trackheight">69</property>
                                <property name="kdenlive:timeline_active">1</property>
                                <property name="kdenlive:collapsed">0</property>
                                <property name="kdenlive:thumbs_format"/>
                                <property name="kdenlive:audio_rec"/>
                                <track hide="video" producer="playlist0"/>
                                <track hide="video" producer="playlist1"/>
                            </tractor>
                            <playlist id="playlist2">
                                <property name="kdenlive:audio_track">1</property>
                            </playlist>
                            <playlist id="playlist3"/>
                            <tractor id="tractor1" in="00:00:00.000">
                                <property name="kdenlive:audio_track">1</property>
                                <property name="kdenlive:trackheight">69</property>
                                <property name="kdenlive:timeline_active">1</property>
                                <property name="kdenlive:collapsed">0</property>
                                <property name="kdenlive:thumbs_format"/>
                                <property name="kdenlive:audio_rec"/>
                                <track hide="video" producer="playlist2"/>
                                <track hide="video" producer="playlist3"/>
                            </tractor>
                            <playlist id="playlist4"/>
                            <playlist id="playlist5"/>
                            <tractor id="tractor2" in="00:00:00.000" out="00:00:12.080">
                                <property name="kdenlive:trackheight">69</property>
                                <property name="kdenlive:timeline_active">1</property>
                                <property name="kdenlive:collapsed">0</property>
                                <property name="kdenlive:thumbs_format"/>
                                <property name="kdenlive:audio_rec"/>
                                <track hide="audio" producer="playlist4"/>
                                <track producer="playlist5"/>
                            </tractor>
                            <playlist id="playlist6">'''
            i = 0
            last_intime = 0
            for sub in session.SUBTITLE['segments']:
                last_intime = sub['start'] - last_intime
                if last_intime:
                    final_xml += '''
                                    <blank length="{intime}"/>'''.format(intime=str(timecode.Timecode('1000', start_seconds=last_intime, fractional=True)))
                final_xml += '''
                                <entry producer="producer{i}" in="{zerotime}" out="{out}">
                                    <property name="kdenlive:id">{id}</property>
                                </entry>'''.format(i=i, id=i + 2, zerotime=str(timecode.Timecode('1000', start_seconds=0.001, fractional=True)), out=str(timecode.Timecode('1000', start_seconds=sub[1], fractional=True)))
                last_intime = sub['end']
                i += 1

            final_xml += '''
                            </playlist>
                            <playlist id="playlist7"/>
                            <tractor id="tractor3" in="00:00:00.000">
                                <property name="kdenlive:trackheight">69</property>
                                <property name="kdenlive:timeline_active">1</property>
                                <property name="kdenlive:collapsed">0</property>
                                <property name="kdenlive:thumbs_format"/>
                                <property name="kdenlive:audio_rec"/>
                                <track hide="audio" producer="playlist6"/>
                                <track producer="playlist7"/>
                            </tractor>
                            <tractor id="tractor4" global_feed="1" in="00:00:00.000" out="00:20:12.120">
                                <track producer="black_track"/>
                                <track producer="tractor0"/>
                                <track producer="tractor1"/>
                                <track producer="tractor2"/>
                                <track producer="tractor3"/>
                            </tractor>
                            </mlt>'''

            with open(filename, mode='w', encoding='utf-8') as txt_file:
                txt_file.write(final_xml)


def save_file(final_file, subtitle_format='USFX', language='en'):
    """Function to save the subtitle project. A subtitles dict and the format is given."""
    if session.SUBTITLE['segments']:
        # if not final_file.lower().endswith('.' + format.lower()):
        #     final_file += '.' + format.lower()

        if not 'format' in session.FORMAT:
            session.FORMAT['format'] = subtitle_format

        if subtitle_format in ['SRT', 'DFXP', 'TTML', 'SAMI', 'SCC', 'VTT']:
            captions = pycaption.CaptionList()
            for sub in session.SUBTITLE['segments']:
                # skip extra blank lines
                nodes = [pycaption.CaptionNode.create_text(sub['text'])]
                caption = pycaption.Caption(start=sub['start'] * 1000000, end=(sub['end']) * 1000000, nodes=nodes)
                captions.append(caption)
            caption_set = pycaption.CaptionSet({language: captions})

            if subtitle_format == 'SRT':
                open(final_file, mode='w', encoding='utf-8').write(pycaption.SRTWriter().write(caption_set))
            elif subtitle_format in ['DFXP', 'TTML']:
                open(final_file, mode='w', encoding='utf-8').write(pycaption.DFXPWriter().write(caption_set))
            elif subtitle_format == 'SAMI':
                open(final_file, mode='w', encoding='utf-8').write(pycaption.SAMIWriter().write(caption_set))
            elif subtitle_format == 'SCC':
                open(final_file, mode='w', encoding='utf-8').write(pycaption.SCCWriter().write(caption_set))
            elif subtitle_format == 'VTT':
                open(final_file, mode='w', encoding='utf-8').write(pycaption.WebVTTWriter().write(caption_set))

        elif subtitle_format in ['ASS', 'SBV', 'XML', 'SUB']:
            if subtitle_format in ['ASS', 'SUB']:
                assfile = pysubs2.SSAFile()
                index = 0
                for sub in reversed(sorted(session.SUBTITLE['segments'])):
                    assfile.insert(
                        index,
                        pysubs2.SSAEvent(
                            start=int(sub['start'] * 1000),
                            end=int(sub['end'] * 1000),
                            text=sub['text'].replace('\n', ' ')
                        )
                    )
                if subtitle_format == 'SUB':
                    assfile.save(final_file, subtitle_format='microdvd')
                else:
                    assfile.save(final_file)
            # else:
            #     if subtitle_format == 'SBV':
            #         from captionstransformer.sbv import Writer
            #     elif subtitle_format == 'XML':
            #         from captionstransformer.transcript import Writer
            #     writer = Writer(open(final_file, mode='w', encoding='utf-8'))
            #     captions = []
            #     for cap in session.SUBTITLE['segments']:
            #         caption = captionstransformer.core.Caption()
            #         caption.start = captionstransformer.core.get_date(second=int(cap[0] // 1), millisecond=int((cap[0] % 1) * 1000))
            #         caption.duration = captionstransformer.core.get_date(second=int(cap[1] // 1), millisecond=int((cap[1] % 1) * 1000)) - captionstransformer.core.get_date()
            #         caption.text = cap[2]
            #         captions.append(caption)
            #     writer.set_captions(captions)
            #     writer.write()

        elif subtitle_format in ['JSON']:
            if session.FORMAT.get('options', {}).get('standard', 'Whisper') == 'Whisper':
                open(final_file, mode='w', encoding='utf-8').write(json.dumps(session.SUBTITLE, indent=4))
            elif session.FORMAT['options'].get('standard', 'Whisper') == 'AD':
                new_json_dict = {
                    'metadata': {
                        'framerate': session.VIDEO.get('framerate', 25),
                        'video_name': os.path.basename(session.VIDEO.get('filepath', ''))
                    },
                    'description_cues': [],
                }
                for i, segment in enumerate(session.SUBTITLE['segments']):
                    new_json_dict['description_cues'].append({
                        'cue_number': i,
                        'text': segment['text'],
                        'start_frame': int(segment['start'] * session.VIDEO.get('framerate', 25)),
                        'end_frame': int(segment['end'] * session.VIDEO.get('framerate', 25)),
                        'start_time_smpte': str(timecode.Timecode(session.VIDEO.get('framerate', 25), start_seconds=segment['start'], fractional=False)),
                        'end_time_smpte': str(timecode.Timecode(session.VIDEO.get('framerate', 25), start_seconds=segment['end'], fractional=False)),
                        'start_time': str(timecode.Timecode(session.VIDEO.get('framerate', 25), start_seconds=segment['start'], fractional=True)),
                        'end_time': str(timecode.Timecode(session.VIDEO.get('framerate', 25), start_seconds=segment['end'], fractional=True)),
                        'speaker': segment.get('speaker', 'A')
                    })

                open(final_file, mode='w', encoding='utf-8').write(json.dumps(new_json_dict, indent=4))


        elif subtitle_format in ['USF']:
            options = session.FORMAT.get('options', {}) if isinstance(session.FORMAT, dict) else {}
            embed_speaker_images = bool(options.get('embed_speaker_images', False))
            embed_audio_clips = bool(options.get('embed_audio_clips', False))

            writer_speakers = {}
            for name, data in session.SPEAKERS.items():
                if not isinstance(data, dict):
                    continue
                entry = {}
                if data.get('color'):
                    entry['color'] = data['color']
                if isinstance(data.get('dubbing'), dict):
                    entry['dubbing'] = data['dubbing']
                if embed_speaker_images and isinstance(data.get('image'), QImage):
                    buf = QByteArray()
                    qbuf = QBuffer(buf)
                    qbuf.open(QIODevice.WriteOnly)
                    if data['image'].save(qbuf, 'PNG'):
                        entry['image_bytes'] = bytes(buf)
                    qbuf.close()
                if entry:
                    writer_speakers[name] = entry

            open(final_file, mode='w', encoding='utf-8').write(usf.USFWriter().write(
                session.SUBTITLE['segments'],
                speakers=writer_speakers,
                language=language,
                embed_audio_clips=embed_audio_clips,
            ))

        elif subtitle_format in ['USFX']:
            usfx_options = _get_usfx_options()

            writer_speakers = {}
            speaker_image_bytes = {}
            for name, data in session.SPEAKERS.items():
                if not isinstance(data, dict):
                    continue
                entry = {}
                if data.get('color'):
                    entry['color'] = data['color']
                if isinstance(data.get('dubbing'), dict):
                    entry['dubbing'] = data['dubbing']
                if usfx_options['include_speaker_images'] and isinstance(data.get('image'), QImage):
                    buf = QByteArray()
                    qbuf = QBuffer(buf)
                    qbuf.open(QIODevice.WriteOnly)
                    if data['image'].save(qbuf, 'PNG'):
                        speaker_image_bytes[name] = bytes(buf)
                    qbuf.close()
                if entry:
                    writer_speakers[name] = entry

            segments_copy = copy.deepcopy(session.SUBTITLE['segments'])
            dub_files_to_include = {}
            for segment in segments_copy:
                for dub in segment.get('dubbing', []) or []:
                    source_path = dub.get('path')
                    uid = dub.get('uid')
                    if source_path and uid and os.path.isfile(source_path):
                        ext = (os.path.splitext(source_path)[1].lstrip('.').lower() or 'wav')
                        arcname = f'assets/dubs/{_safe_asset_name(uid)}.{ext}'
                        dub_files_to_include[source_path] = arcname
                        dub['path'] = arcname

            # Collect optional bundled assets (video + separation caches + waveform cache).
            video_path = session.VIDEO.get('filepath', '') if isinstance(session.VIDEO, dict) else ''
            video_cache_key = utils.get_cache_key(video_path) if video_path else None
            extra_files_to_include = {}  # source path -> arcname

            if usfx_options['include_original_video'] and video_path and os.path.isfile(video_path):
                extra_files_to_include[video_path] = f'assets/video/{_safe_asset_name(os.path.basename(video_path))}'

            if video_cache_key:
                if usfx_options['include_waveform_cache']:
                    wf_source = os.path.join(session.PATH_SUBTITLD_USER_CACHE, 'waveform', f'{video_cache_key}_waveform.npy')
                    if os.path.isfile(wf_source):
                        extra_files_to_include[wf_source] = 'assets/waveform.npy'
                if usfx_options['include_original_audio']:
                    src = os.path.join(session.PATH_SUBTITLD_DATA_AUDIOSEPARATION, f'{video_cache_key}_original.flac')
                    if os.path.isfile(src):
                        extra_files_to_include[src] = 'assets/audio/original.flac'
                if usfx_options['include_processed_audio']:
                    for kind in ('vocals', 'background'):
                        src = os.path.join(session.PATH_SUBTITLD_DATA_AUDIOSEPARATION, f'{video_cache_key}_{kind}.flac')
                        if os.path.isfile(src):
                            extra_files_to_include[src] = f'assets/audio/{kind}.flac'

            xml_content = usf.USFWriter().write(
                segments_copy,
                speakers=writer_speakers,
                language=language,
                embed_audio_clips=False,
            )

            def _mime_for(path_in_zip):
                ext = path_in_zip.rsplit('.', 1)[-1].lower()
                return {
                    'usf': 'application/x-usf+xml',
                    'png': 'image/png',
                    'wav': 'audio/wav',
                    'flac': 'audio/flac',
                    'mp3': 'audio/mpeg',
                    'mp4': 'video/mp4',
                    'mkv': 'video/x-matroska',
                    'mov': 'video/quicktime',
                    'webm': 'video/webm',
                    'ogv': 'video/ogg',
                    'npy': 'application/octet-stream',
                }.get(ext, 'application/octet-stream')

            manifest_entries = [('subtitles.usf', 'application/x-usf+xml')]
            for name in speaker_image_bytes:
                manifest_entries.append((f'assets/speakers/{_safe_asset_name(name)}.png', 'image/png'))
            for arc in dub_files_to_include.values():
                manifest_entries.append((arc, _mime_for(arc)))
            for arc in extra_files_to_include.values():
                manifest_entries.append((arc, _mime_for(arc)))

            manifest_lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                              '<manifest version="1.0">',
                              '  <format>USFX</format>',
                              '  <generator>Subtitld</generator>']
            if video_path:
                escaped_path = video_path.replace('&', '&amp;').replace('"', '&quot;')
                escaped_basename = os.path.basename(video_path).replace('&', '&amp;').replace('"', '&quot;')
                manifest_lines.append(f'  <source path="{escaped_path}" basename="{escaped_basename}"/>')
            mvs = session.VIDEO.get('music_voice_separation') if isinstance(session.VIDEO, dict) else None
            if isinstance(mvs, dict) and 'volume' in mvs:
                try:
                    manifest_lines.append(f'  <voicemix volume="{float(mvs["volume"]):.4f}"/>')
                except (TypeError, ValueError):
                    pass
            for path, mime in manifest_entries:
                manifest_lines.append(f'  <entry path="{path}" type="{mime}"/>')
            manifest_lines.append('</manifest>')
            manifest_xml = '\n'.join(manifest_lines)

            tmp_path = final_file + '.tmp'
            try:
                with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    zf.writestr('subtitles.usf', xml_content)
                    zf.writestr('manifest.xml', manifest_xml)
                    for source, arc in dub_files_to_include.items():
                        zf.write(source, arc)
                    for name, img_bytes in speaker_image_bytes.items():
                        zf.writestr(f'assets/speakers/{_safe_asset_name(name)}.png', img_bytes)
                    for source, arc in extra_files_to_include.items():
                        zf.write(source, arc)
                os.replace(tmp_path, final_file)
            except Exception:
                if os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass
                raise


def peek_usfx_video(usfx_path):
    """Inspect a USFX bundle and return the resolved video path without
    loading the full project. Used by the start screen so it can skip the
    'select video' prompt when the project remembers (or bundles) it.
    Returns the absolute path on success, otherwise None."""
    if not usfx_path or not os.path.isfile(usfx_path):
        return None
    try:
        with zipfile.ZipFile(usfx_path, 'r') as zf:
            usfx_dir = os.path.dirname(os.path.abspath(usfx_path))

            # Bundled video first.
            for name in zf.namelist():
                if name.startswith('assets/video/') and not name.endswith('/'):
                    extract_dir = _usfx_extract_dir(usfx_path)
                    target = os.path.join(extract_dir, name)
                    if not os.path.isfile(target):
                        zf.extract(name, extract_dir)
                    if os.path.isfile(target):
                        return target

            try:
                manifest_data = zf.read('manifest.xml').decode('utf-8')
            except KeyError:
                return None

        soup = BeautifulSoup(manifest_data, 'lxml-xml')
        source = soup.find('source')
        if source is None:
            return None

        recorded_path = source.get('path', '') or ''
        recorded_basename = source.get('basename', '') or os.path.basename(recorded_path)

        for candidate in (recorded_path, os.path.join(usfx_dir, recorded_basename) if recorded_basename else ''):
            if candidate and os.path.isfile(candidate):
                return candidate
    except Exception:
        return None
    return None


def _autosave_filename_stem():
    if session.SUBTITLE.get('filepath'):
        stem = os.path.basename(session.SUBTITLE['filepath']).rsplit('.', 1)[0]
        if stem:
            return stem
    if session.VIDEO.get('filepath'):
        return os.path.basename(session.VIDEO['filepath']).rsplit('.', 1)[0]
    return ''


def _prune_backups(stem):
    max_count = int(session.CONFIG.get('autosave', {}).get('backup_max_count', 20))
    if max_count <= 0:
        return
    backup_dir = str(session.PATH_SUBTITLD_DATA_BACKUP)
    try:
        candidates = [
            os.path.join(backup_dir, name)
            for name in os.listdir(backup_dir)
            if name.startswith(stem + '_') and name.endswith('.usfx')
        ]
    except OSError:
        return
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for stale in candidates[max_count:]:
        try:
            os.unlink(stale)
        except OSError:
            pass


class SaveFileThread(QThread):
    """Run `save_file()` on a background thread so the UI stays responsive
    while USFX bundles are zipped and assets are written to disk."""
    save_finished = Signal(str, bool, str)  # (filepath, success, error)

    def __init__(self, final_file, subtitle_format, language, parent=None):
        super().__init__(parent)
        self._final_file = final_file
        self._subtitle_format = subtitle_format
        self._language = language

    def run(self):
        try:
            save_file(self._final_file, self._subtitle_format, self._language)
            self.save_finished.emit(self._final_file, True, '')
        except Exception as exc:
            self.save_finished.emit(self._final_file, False, str(exc))


_active_save_threads = []


def wait_for_save_threads():
    """Block until every in-flight save thread finishes. Call before exit so
    autosave/manual saves still flush even when the user closes the window
    while a write is queued or running."""
    for thread in list(_active_save_threads):
        try:
            thread.wait()
        except RuntimeError:
            pass


def save_file_async(final_file, subtitle_format='USFX', language='en', on_done=None, parent=None):
    """Spawn a `SaveFileThread`, optionally wire `on_done(path, ok, err)`,
    keep a reference so the QThread isn't garbage-collected mid-save."""
    thread = SaveFileThread(final_file, subtitle_format, language, parent=parent)

    def _cleanup(path, success, error):
        if on_done is not None:
            on_done(path, success, error)
        if thread in _active_save_threads:
            _active_save_threads.remove(thread)
        thread.deleteLater()

    thread.save_finished.connect(_cleanup, Qt.QueuedConnection)
    _active_save_threads.append(thread)
    thread.start()
    return thread


def autosave_backup_timer_timeout(force=False):
    if not session.SUBTITLE:
        return
    if not force and not session.AUTOSAVE_BACKUP_DIRTY:
        return
    stem = _autosave_filename_stem()
    if not stem:
        return
    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

    def _on_done(path, success, _error):
        if not success:
            return
        _prune_backups(stem)
        session.AUTOSAVE_BACKUP_DIRTY = False
        session.AUTOSAVE_LAST_BACKUP = datetime.datetime.now()
        for callback in session._autosave_status_callbacks:
            callback()

    save_file_async(
        os.path.join(str(session.PATH_SUBTITLD_DATA_BACKUP), f'{stem}_{timestamp}.usfx'),
        'USFX',
        session.CONFIG.get('selected_language', 'en'),
        on_done=_on_done,
    )


def autosave_original_timer_timeout():
    if not session.SUBTITLE:
        return
    if not session.UNSAVED:
        return
    if not session.SUBTITLE.get('filepath', '').lower().endswith('.usfx'):
        return

    def _on_done(_path, success, _error):
        if not success:
            return
        session.set_unsaved(False)
        session.AUTOSAVE_BACKUP_DIRTY = False
        session.AUTOSAVE_LAST_ORIGINAL = datetime.datetime.now()
        session.notify_save_success()
        for callback in session._autosave_status_callbacks:
            callback()

    save_file_async(
        session.SUBTITLE['filepath'],
        'USFX',
        session.CONFIG['selected_language'],
        on_done=_on_done,
    )
