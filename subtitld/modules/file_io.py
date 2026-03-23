import os
import hashlib
from docx import Document
import json
import pycaption
from pycaption.exceptions import CaptionReadSyntaxError, CaptionReadNoCaptions
import chardet
import pysubs2
import datetime

from PySide6.QtWidgets import QFileDialog
from PySide6.QtCore import QThread, Signal

from subtitld.modules import timecode
from subtitld.modules import session
from subtitld.modules import waveform
from subtitld.modules import usf

from subtitld.interface import timeline
# from subtitld.interface import productionscreen
# from subtitld.interface import startscreen
# from subtitld.interface import subtitles_panel_widget_qlistwidget
# from subtitld.interface import subtitles_panel
# from subtitld.interface import subtitles_panel_info
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
            segments_list = usf.USFReader().read(open(subtitle_file).read())

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


def save_file(final_file, subtitle_format='USF', language='en'):
    """Function to save the subtitle project. A subtitles dict and the format is given."""
    if session.SUBTITLE['segments']:
        # if not final_file.lower().endswith('.' + format.lower()):
        #     final_file += '.' + format.lower()

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
            open(final_file, mode='w', encoding='utf-8').write(json.dumps(session.SUBTITLE, indent=4))

        elif subtitle_format in ['USF']:
            open(final_file, mode='w', encoding='utf-8').write(usf.USFWriter().write(session.SUBTITLE['segments']))


def autosave_timer_timeout():
    if session.SUBTITLE:
        if not 'filepath' in session.SUBTITLE:
            filename = os.path.basename(session.VIDEO['filepath']).rsplit('.', 1)[0]
        else:
            filename = os.path.basename(session.SUBTITLE['filepath']).rsplit('.', 1)[0]
        if not filename:
            filename = os.path.basename(session.VIDEO['filepath']).rsplit('.', 1)[0]
        save_file(os.path.join(session.PATH_SUBTITLD_DATA_BACKUP, filename + '_' + datetime.datetime.now().strftime("%Y%m%d%H%M%S") + '.usf'))
