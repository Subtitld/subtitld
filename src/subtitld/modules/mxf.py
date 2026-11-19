"""MXF (.mxf) carrying subtitles: SMPTE ST 429-5 timed text track files
(a cinema's SMPTE DCP subtitles, and IMF's IMSC 1.1), and EBU STL in MXF
(SMPTE ST 2075). Written and read by Subtitld itself, KLV by KLV.

MXF (SMPTE ST 377-1) is a sequence of KLV packets: a 16-byte key (a SMPTE
Universal Label), a BER length and the value. A file is cut into
partitions, each opened by a partition pack; the header partition's
metadata (a primer pack, then local sets that refer to each other by
InstanceUID) says what the file holds; a random index pack (RIP) at the
end lists where every partition starts.

ST 429-5 (its 2023 edition, read at pub.smpte.org) puts one XML document,
the timed text resource, clip-wrapped as the single essence element of a
Generic Container in a body partition, and each resource the XML names (a
font, a PNG) in a Generic Stream partition of its own (ST 410), its data
one KLV under the default Generic Stream data element key. The header's
file package is described by a TimedTextDescriptor (ResourceID, UCS
encoding, the XML's namespace) with a TimedTextResourceSubDescriptor for
each resource (its UUID, MIME type and the BodySID of its partition). One
index table segment points at the XML; the RIP lists the Generic Stream
partitions. The labels and local tags are asdcplib's (MDD.cpp), checked
against SMPTE's registers (Labels, Elements, Groups).

The layout follows asdcplib, the reference implementation:

- a DCP's track file (AS_DCP_TimedText.cpp: SMPTE ST 428-7 subtitles,
  OP-Atom, MXF 2004) is header, body partition with the XML, a Generic
  Stream partition a resource, and the footer holding the index; timecode
  tracks beside the data tracks, as ST 429-3 has them;
- IMF's (AS_02_TimedText.cpp: an IMSC 1.1 document, the profile ST 2067-2
  takes; OP1a, MXF 2011, no timecode track) is header, body partition
  with the XML, a partition of its own for the index, the resources, and
  a footer without one.

EBU STL in MXF (SMPTE ST 2075:2013): the whole STL file, as it is, one
KLV in a Generic Stream partition, under the same default data element
key (byte 12 0x09: a byte stream, its KL not part of the data; byte 13
0x01: not divided, not frame-wrapped); its partition pack and the header
naming the essence container "MXF-GS EBU-t3264 STL Byte Stream"; an OP1a
file with one file package whose data track (its TrackNumber the last
four bytes of that key) is described by an STL Descriptor (an Event Text
Descriptor: the kind, EBU-t3264 STL Subtitle Essence, and the language as
an RFC 5646 tag; the STL Reference Point Timecode, the GSI's TCP as a
frame count) and whose timecode track starts at that same frame, at the
edit rate the GSI's DFC names (25/1, or 30000/1001 for STL30.01, which
Subtitld writes for 29.97 video). The Essence Container Data links that
package to the stream's SID.

Reading takes any of them, and an STL carried in a video's MXF too: the
primer, every local set, which partition each KLV is in, the essence
element and the Generic Stream data by their keys. An encrypted file
(ST 429-6) is refused, as Subtitld has no key to it.

What is not done: encryption; a STL Sub-Descriptor's further languages
(read as the STL's lines; not written); several STL files in one MXF (the
first is read); fonts in a DCP (Subtitld has no font file to wrap: the
projector's own draws the text).
"""

import datetime
import io
import struct
import uuid


def _ul(text):
    return bytes.fromhex(text.replace('.', ''))


# Partition packs and the packs around them (asdcplib MDD.cpp; ST 377-1).
_PARTITION = _ul('060e2b34.02050101.0d010201.01')       # + kind, status, 00
HEADER_CLOSED_COMPLETE = _ul('060e2b34.02050101.0d010201.01020400')
BODY_CLOSED_COMPLETE = _ul('060e2b34.02050101.0d010201.01030400')
GENERIC_STREAM_PARTITION = _ul('060e2b34.02050101.0d010201.01031100')
FOOTER_COMPLETE = _ul('060e2b34.02050101.0d010201.01040400')
PRIMER = _ul('060e2b34.02050101.0d010201.01050100')
RIP = _ul('060e2b34.02050101.0d010201.01110100')
FILL = _ul('060e2b34.01010102.03010210.01000000')
INDEX_SEGMENT = _ul('060e2b34.02530101.0d010201.01100100')
ENCRYPTED_TRIPLET = _ul('060e2b34.02040107.0d010301.027e0100')

# Essence: the XML (ST 429-5 Table A.2, element 1), a Generic Stream's
# data (ST 410, as 429-5 Table 1 and ST 2075 8.1 set it).
TIMED_TEXT_ELEMENT = _ul('060e2b34.01020101.0d010301.17010b01')
GENERIC_STREAM_DATA = _ul('060e2b34.0101010c.0d010509.01000000')

# Labels.
OP_ATOM = _ul('060e2b34.04010102.0d010201.10000000')
OP_1A = _ul('060e2b34.04010101.0d010201.01010100')
GC_MULTI = _ul('060e2b34.04010103.0d010301.027f0100')
TIMED_TEXT_CONTAINER = _ul('060e2b34.0401010a.0d010301.02130101')
STL_CONTAINER = _ul('060e2b34.0401010a.0d010301.03010000')      # MXF-GS EBU-t3264 STL Byte Stream
STL_SUBTITLE_KIND = _ul('060e2b34.0401010d.04030101.01000000')  # EBU-t3264 STL Subtitle Essence
STL_CAPTIONS_KIND = _ul('060e2b34.0401010d.04030102.01000000')
DATA_DEF = _ul('060e2b34.04010101.01030202.03000000')
TIMECODE_DEF = _ul('060e2b34.04010101.01030201.01000000')

# Local sets: 06.0e.2b.34.02.53.01.01.0d.01.01.01.01.01.xx.00.
_SET = _ul('060e2b34.02530101.0d010101.0101')
PREFACE, IDENTIFICATION, CONTENT_STORAGE, CONTAINER_DATA = 0x2f, 0x30, 0x18, 0x23
MATERIAL_PACKAGE, SOURCE_PACKAGE, TRACK, SEQUENCE, SOURCE_CLIP, TIMECODE = 0x36, 0x37, 0x3b, 0x0f, 0x11, 0x14
TIMED_TEXT_DESCRIPTOR, TIMED_TEXT_RESOURCE = 0x64, 0x65
STL_DESCRIPTOR, STL_SUB_DESCRIPTOR = 0x70, 0x71

# Properties: their ULs and the local tags ST 377-1 gives them; None is
# a tag the primer pack allocates (dynamic).
_PROPERTIES = {
    'InstanceUID': ('060e2b34.01010101.01011502.00000000', 0x3c0a),
    'GenerationUID': ('060e2b34.01010102.05200701.08000000', 0x0102),
    'LastModifiedDate': ('060e2b34.01010102.07020110.02040000', 0x3b02),
    'Version': ('060e2b34.01010102.03010201.05000000', 0x3b05),
    'ObjectModelVersion': ('060e2b34.01010102.03010201.04000000', 0x3b07),
    'PrimaryPackage': ('060e2b34.01010104.06010104.01080000', 0x3b08),
    'Identifications': ('060e2b34.01010102.06010104.06040000', 0x3b06),
    'ContentStorage': ('060e2b34.01010102.06010104.02010000', 0x3b03),
    'OperationalPattern': ('060e2b34.01010105.01020203.00000000', 0x3b09),
    'EssenceContainers': ('060e2b34.01010105.01020210.02010000', 0x3b0a),
    'DMSchemes': ('060e2b34.01010105.01020210.02020000', 0x3b0b),
    'ThisGenerationUID': ('060e2b34.01010102.05200701.01000000', 0x3c09),
    'CompanyName': ('060e2b34.01010102.05200701.02010000', 0x3c01),
    'ProductName': ('060e2b34.01010102.05200701.03010000', 0x3c02),
    'VersionString': ('060e2b34.01010102.05200701.05010000', 0x3c04),
    'ProductUID': ('060e2b34.01010102.05200701.07000000', 0x3c05),
    'ModificationDate': ('060e2b34.01010102.07020110.02030000', 0x3c06),
    'Platform': ('060e2b34.01010102.05200701.06010000', 0x3c08),
    'Packages': ('060e2b34.01010102.06010104.05010000', 0x1901),
    'EssenceContainerData': ('060e2b34.01010102.06010104.05020000', 0x1902),
    'LinkedPackageUID': ('060e2b34.01010102.06010106.01000000', 0x2701),
    'IndexSID': ('060e2b34.01010104.01030405.00000000', 0x3f06),
    'BodySID': ('060e2b34.01010104.01030404.00000000', 0x3f07),
    'PackageUID': ('060e2b34.01010101.01011510.00000000', 0x4401),
    'Name': ('060e2b34.01010101.01030302.01000000', 0x4402),
    'PackageCreationDate': ('060e2b34.01010102.07020110.01030000', 0x4405),
    'PackageModifiedDate': ('060e2b34.01010102.07020110.02050000', 0x4404),
    'Tracks': ('060e2b34.01010102.06010104.06050000', 0x4403),
    'Descriptor': ('060e2b34.01010102.06010104.02030000', 0x4701),
    'TrackID': ('060e2b34.01010102.01070101.00000000', 0x4801),
    'TrackNumber': ('060e2b34.01010102.01040103.00000000', 0x4804),
    'TrackName': ('060e2b34.01010102.01070102.01000000', 0x4802),
    'Sequence': ('060e2b34.01010102.06010104.02040000', 0x4803),
    'EditRate': ('060e2b34.01010102.05300405.00000000', 0x4b01),
    'Origin': ('060e2b34.01010102.07020103.01030000', 0x4b02),
    'DataDefinition': ('060e2b34.01010102.04070100.00000000', 0x0201),
    'Duration': ('060e2b34.01010102.07020201.01030000', 0x0202),
    'StructuralComponents': ('060e2b34.01010102.06010104.06090000', 0x1001),
    'StartPosition': ('060e2b34.01010102.07020103.01040000', 0x1201),
    'SourcePackageID': ('060e2b34.01010102.06010103.01000000', 0x1101),
    'SourceTrackID': ('060e2b34.01010102.06010103.02000000', 0x1102),
    'RoundedTimecodeBase': ('060e2b34.01010102.04040101.02060000', 0x1502),
    'StartTimecode': ('060e2b34.01010102.07020103.01050000', 0x1501),
    'DropFrame': ('060e2b34.01010101.04040101.05000000', 0x1503),
    'LinkedTrackID': ('060e2b34.01010105.06010103.05000000', 0x3006),
    'SampleRate': ('060e2b34.01010101.04060101.00000000', 0x3001),
    'ContainerDuration': ('060e2b34.01010101.04060102.00000000', 0x3002),
    'EssenceContainer': ('060e2b34.01010102.06010104.01020000', 0x3004),
    'SubDescriptors': ('060e2b34.01010109.06010104.06100000', None),
    'ResourceID': ('060e2b34.0101010c.01011512.00000000', None),
    'UCSEncoding': ('060e2b34.0101010c.04090500.00000000', None),
    'NamespaceURI': ('060e2b34.01010108.01020105.01000000', None),
    'AncillaryResourceID': ('060e2b34.0101010c.01011513.00000000', None),
    'MIMEMediaType': ('060e2b34.01010107.04090201.00000000', None),
    # The same label as BodySID's, so the same tag (as asdcplib's primer
    # gives it): a resource's partition is a body.
    'EssenceStreamID': ('060e2b34.01010104.01030404.00000000', 0x3f07),
    'EventTextKind': ('060e2b34.0101010e.03020108.01000000', None),
    'EventTextLanguageCode': ('060e2b34.0101010d.03010102.02150000', None),
    'STLReferencePointTimecode': ('060e2b34.0101010e.07020102.02020000', None),
    'STLLineNumber': ('060e2b34.0101010e.03020108.02000000', None),
}
_UL_OF = {name: _ul(ul) for name, (ul, _tag) in _PROPERTIES.items()}

PRODUCT_UID = uuid.UUID('a1b0c4e3-8f8e-4d5c-9b3e-5d7f2a6c1e90')
DCP, IMF = 'dcp', 'imf'


def _versionless(key):
    """A label with its version byte (the 8th) left out, as labels are
    compared: a newer register's version of the same label is the same."""
    return bytes(key[:7]) + bytes(key[8:])


# Coding.
def _ber(length):
    """A BER length, four bytes long as asdcplib writes them (eight past
    16 MiB)."""
    if length < 1 << 24:
        return bytes([0x83]) + length.to_bytes(3, 'big')
    return bytes([0x88]) + length.to_bytes(8, 'big')


def _klv(key, value):
    return key + _ber(len(value)) + value


def _utf16(text):
    return str(text).encode('utf-16-be')


def _rational(rate):
    numerator, denominator = rate
    return struct.pack('>ii', int(numerator), int(denominator))


def _timestamp(moment):
    return struct.pack('>HBBBBBB', moment.year, moment.month, moment.day, moment.hour, moment.minute, moment.second,
                       moment.microsecond // 4000)


def _batch(items, size=16):
    return struct.pack('>II', len(items), size) + b''.join(items)


def _umid(material):
    """A basic UMID (SMPTE 330) for a package, made from a UUID as
    asdcplib's MakeUMID makes one."""
    return bytes([0x06, 0x0a, 0x2b, 0x34, 0x01, 0x01, 0x01, 0x05, 0x01, 0x01, 0x0f, 0x20, 0x13, 0, 0, 0]) + material.bytes


class _Primer:
    """The local tags a file's header metadata uses, and the ULs they
    stand for; dynamic ones are given out from 0xFFFF down."""

    def __init__(self):
        self.tags = {}
        self.next = 0xffff

    def tag(self, name):
        if name not in self.tags:
            static = _PROPERTIES[name][1]
            if static is None:
                static, self.next = self.next, self.next - 1
            self.tags[name] = static
        return self.tags[name]

    def pack(self):
        entries = {tag: struct.pack('>H', tag) + _UL_OF[name] for name, tag in self.tags.items()}
        return _klv(PRIMER, _batch(list(entries.values()), 18))


def _local_set(kind, items, primer):
    """A local set of `kind` (a key's 15th byte): [(property, value bytes)]."""
    body = b''.join(struct.pack('>HH', primer.tag(name), len(value)) + value for name, value in items)
    return _klv(_SET + bytes([kind, 0]), body)


def _partition(key, this, previous, footer, header_bytes, index_bytes, index_sid, body_sid, pattern, containers,
               minor=2):
    value = struct.pack('>HHIQQQQQIQI', 1, minor, 1, this, previous, footer, header_bytes, index_bytes, index_sid, 0,
                        body_sid) + pattern + _batch(containers)
    return _klv(key, value)


def _index_segment(rate, index_sid=129, body_sid=1):
    """ST 429-5 7.2's index: one segment, one entry pointing at the XML
    (StreamOffset 0), no delta entries, EditUnitByteCount 0."""
    items = [(0x3c0a, uuid.uuid4().bytes), (0x3f0b, _rational(rate)), (0x3f0c, struct.pack('>q', 0)),
             (0x3f0d, struct.pack('>q', 1)), (0x3f05, struct.pack('>I', 0)), (0x3f06, struct.pack('>I', index_sid)),
             (0x3f07, struct.pack('>I', body_sid)), (0x3f08, bytes([0])), (0x3f0e, bytes([0])),
             (0x3f09, _batch([], 6)), (0x3f0a, _batch([struct.pack('>bbBQ', 0, 0, 0, 0)], 11))]
    return _klv(INDEX_SEGMENT, b''.join(struct.pack('>HH', tag, len(value)) + value for tag, value in items))


def _rip(pairs):
    body = b''.join(struct.pack('>IQ', sid, offset) for sid, offset in pairs)
    length = 16 + 4 + len(body) + 4
    return RIP + _ber(len(body) + 4) + body + struct.pack('>I', length)


class _Metadata:
    """The header metadata of one essence track: Preface, Identification,
    ContentStorage, Essence Container Data, a material package and a file
    package with their tracks (asdcplib's InitHeader and AddSourceClip),
    and the file package's descriptor and sub-descriptors."""

    def __init__(self, minor, pattern, containers):
        self.primer = _Primer()
        self.sets = []
        self.minor = minor
        self.pattern = pattern
        self.containers = containers
        self.now = _timestamp(datetime.datetime.now(datetime.timezone.utc))

    def add(self, kind, items):
        instance = uuid.uuid4().bytes
        self.sets.append((kind, [('InstanceUID', instance)] + items))
        return instance

    def track(self, track_id, number, name, rate, data_definition, duration, component):
        """A track with its sequence of one component, `component` the
        component's own items; the track's InstanceUID."""
        clip = self.add(component[0], [('DataDefinition', data_definition), ('Duration', struct.pack('>q', duration))]
                        + component[1])
        sequence = self.add(SEQUENCE, [('DataDefinition', data_definition), ('Duration', struct.pack('>q', duration)),
                                       ('StructuralComponents', _batch([clip]))])
        return self.add(TRACK, [('TrackID', struct.pack('>I', track_id)), ('TrackNumber', struct.pack('>I', number)),
                                ('TrackName', _utf16(name)), ('EditRate', _rational(rate)),
                                ('Origin', struct.pack('>q', 0)), ('Sequence', sequence)])

    def build(self, descriptor_kind, descriptor_items, subdescriptors, rate, duration, track_name, track_number,
              timecode_start=None, timecode_base=None, package_name='', body_sid=1, index_sid=129, material=None):
        """Every set, the descriptor `descriptor_items` (and those of
        `subdescriptors`, each [(property, value)]) describing the file
        package's data track; a timecode track first where
        `timecode_base` is given. The header metadata's bytes."""
        material = material or uuid.uuid4()
        file_umid = _umid(material)
        material_umid = _umid(uuid.uuid4())
        tracks_file, tracks_material, track_id = [], [], 1
        if timecode_base:
            component = (TIMECODE, [('RoundedTimecodeBase', struct.pack('>H', timecode_base)),
                                    ('StartTimecode', struct.pack('>q', timecode_start or 0)), ('DropFrame', bytes([0]))])
            tracks_material.append(self.track(track_id, 0, 'Timecode Track', rate, TIMECODE_DEF, duration, component))
            tracks_file.append(self.track(track_id, 0, 'Timecode Track', rate, TIMECODE_DEF, duration, component))
            track_id += 1
        tracks_material.append(self.track(track_id, 0, track_name, rate, DATA_DEF, duration, (SOURCE_CLIP, [
            ('StartPosition', struct.pack('>q', 0)), ('SourcePackageID', file_umid),
            ('SourceTrackID', struct.pack('>I', track_id))])))
        tracks_file.append(self.track(track_id, track_number, track_name, rate, DATA_DEF, duration, (SOURCE_CLIP, [
            ('StartPosition', struct.pack('>q', 0)), ('SourcePackageID', bytes(32)),
            ('SourceTrackID', struct.pack('>I', 0))])))
        subs = [self.add(kind, items) for kind, items in subdescriptors]
        items = [('LinkedTrackID', struct.pack('>I', track_id)), ('SampleRate', _rational(rate)),
                 ('ContainerDuration', struct.pack('>q', duration))] + descriptor_items
        if subs:
            items.append(('SubDescriptors', _batch(subs)))
        descriptor = self.add(descriptor_kind, items)
        dates = [('PackageCreationDate', self.now), ('PackageModifiedDate', self.now)]
        material_package = self.add(MATERIAL_PACKAGE, [('PackageUID', material_umid), ('Name', _utf16('Material Package'))]
                                    + dates + [('Tracks', _batch(tracks_material))])
        file_package = self.add(SOURCE_PACKAGE, [('PackageUID', file_umid), ('Name', _utf16(package_name))] + dates
                                + [('Tracks', _batch(tracks_file)), ('Descriptor', descriptor)])
        container = self.add(CONTAINER_DATA, [('LinkedPackageUID', file_umid), ('IndexSID', struct.pack('>I', index_sid)),
                                              ('BodySID', struct.pack('>I', body_sid))])
        storage = self.add(CONTENT_STORAGE, [('Packages', _batch([material_package, file_package])),
                                             ('EssenceContainerData', _batch([container]))])
        from subtitld import __version__
        identification = self.add(IDENTIFICATION, [
            ('ThisGenerationUID', uuid.uuid4().bytes), ('CompanyName', _utf16('Subtitld')),
            ('ProductName', _utf16('Subtitld')), ('VersionString', _utf16(__version__)),
            ('ProductUID', PRODUCT_UID.bytes), ('ModificationDate', self.now), ('Platform', _utf16('Subtitld'))])
        preface = [('LastModifiedDate', self.now), ('Version', struct.pack('>H', 0x0100 | self.minor)),
                   ('ObjectModelVersion', struct.pack('>I', 1)), ('Identifications', _batch([identification])),
                   ('ContentStorage', storage), ('OperationalPattern', self.pattern),
                   ('EssenceContainers', _batch(self.containers)), ('DMSchemes', _batch([])),
                   ('PrimaryPackage', file_package)]
        # The Preface first, as ST 377-1 asks.
        self.sets.insert(0, (PREFACE, [('InstanceUID', uuid.uuid4().bytes)] + preface))
        sets = b''.join(_local_set(kind, items, self.primer) for kind, items in self.sets)
        return self.primer.pack() + sets


def _assemble(header_metadata, pattern, containers, minor, body, after_body, index, index_in_footer):
    """The file: a closed complete header partition with
    `header_metadata`; `body` [(key, sid, KLV bytes)] partitions, then
    `after_body` the same; the index segment in the footer
    (`index_in_footer`) or in a partition of its own after the first
    body partition; and the RIP. Offsets are worked out first, then the
    packs written with them."""
    def pack(key, this, previous, footer, header_bytes=0, index_bytes=0, index_sid=0, body_sid=0):
        return _partition(key, this, previous, footer, header_bytes, index_bytes, index_sid, body_sid, pattern,
                          containers, minor)
    layout = [('header', HEADER_CLOSED_COMPLETE, 0, header_metadata)]
    for position, (key, sid, data) in enumerate(body + after_body):
        layout.append(('body', key, sid, data))
        if position == 0 and not index_in_footer and index is not None:
            layout.append(('index', BODY_CLOSED_COMPLETE, 0, index))
    footer_payload = index if index_in_footer and index is not None else b''
    layout.append(('footer', FOOTER_COMPLETE, 0, footer_payload))
    pack_size = len(pack(HEADER_CLOSED_COMPLETE, 0, 0, 0))
    offsets, position = [], 0
    for _role, _key, _sid, payload in layout:
        offsets.append(position)
        position += pack_size + len(payload)
    footer_at = offsets[-1]
    out, pairs = [], []
    for number, (role, key, sid, payload) in enumerate(layout):
        this = offsets[number]
        previous = offsets[number - 1] if number else 0
        if role == 'header':
            out.append(pack(key, this, 0, footer_at, header_bytes=len(payload)))
        elif role == 'body':
            out.append(pack(key, this, previous, footer_at, body_sid=sid))
        elif role == 'index':
            out.append(pack(key, this, previous, footer_at, index_bytes=len(payload), index_sid=129))
        else:
            out.append(pack(key, this, previous, footer_at, index_bytes=len(payload), index_sid=129 if payload else 0))
        out.append(payload)
        pairs.append((sid if role == 'body' else 0, this))
    out.append(_rip(pairs))
    return b''.join(out)


def write_timed_text(document, resources=(), rate=(24, 1), duration=0, namespace='', resource_id=None, flavour=DCP):
    """An ST 429-5 timed text track file: `document` (the XML's bytes),
    `resources` [(UUID, MIME type, bytes)] each in a Generic Stream
    partition of its own (SIDs from 10, as asdcplib numbers them), at edit
    rate `rate` (numerator, denominator) for `duration` edit units, the
    XML's namespace `namespace` and its UUID `resource_id` (a new one where
    not given). `flavour` DCP lays it out as a D-Cinema track file (OP-Atom,
    MXF 2004, timecode tracks, the index in the footer), IMF as IMF's
    (OP1a, MXF 2011, no timecode, the index in a partition of its own)."""
    resource_id = resource_id or uuid.uuid4()
    dcp = flavour != IMF
    pattern = OP_ATOM if dcp else OP_1A
    minor = 2 if dcp else 3
    containers = [GC_MULTI, TIMED_TEXT_CONTAINER]
    metadata = _Metadata(minor, pattern, containers)
    subdescriptors = [(TIMED_TEXT_RESOURCE, [('AncillaryResourceID', identifier.bytes), ('MIMEMediaType', _utf16(kind)),
                                             ('EssenceStreamID', struct.pack('>I', 10 + number))])
                      for number, (identifier, kind, _data) in enumerate(resources)]
    descriptor = [('EssenceContainer', TIMED_TEXT_CONTAINER), ('ResourceID', resource_id.bytes),
                  ('UCSEncoding', _utf16('UTF-8')), ('NamespaceURI', _utf16(namespace))]
    rounded = int(round(rate[0] / rate[1]))
    header = metadata.build(TIMED_TEXT_DESCRIPTOR, descriptor, subdescriptors, rate, int(duration), 'Data Track',
                            int.from_bytes(TIMED_TEXT_ELEMENT[12:16], 'big'),
                            timecode_base=rounded if dcp else None,
                            package_name='File Package: SMPTE 429-5 clip wrapping of D-Cinema Timed Text data'
                            if dcp else 'File Package: PROTOTYPE SMPTE ST 429-5 / ST 2067-5 clip wrapping of IMF Timed Text data',
                            material=resource_id)
    body = [(BODY_CLOSED_COMPLETE, 1, _klv(TIMED_TEXT_ELEMENT, bytes(document)))]
    streams = [(GENERIC_STREAM_PARTITION, 10 + number, _klv(GENERIC_STREAM_DATA, bytes(data)))
               for number, (_identifier, _kind, data) in enumerate(resources)]
    return _assemble(header, pattern, containers, minor, body, streams, _index_segment(rate), index_in_footer=dcp)


STL_RATES = {'STL25.01': (25, 1), 'STL30.01': (30000, 1001), 'STL24.01': (24, 1), 'STL23.01': (24000, 1001),
             'STL50.01': (50, 1), 'STL60.01': (60000, 1001)}


def write_stl(stl, language='', reference=0, duration=0, captions=False):
    """EBU STL in MXF (ST 2075): the STL file `stl` (its bytes, as they
    are) in a Generic Stream partition, the edit rate its DFC names,
    `language` an RFC 5646 tag, `reference` the GSI's TCP as a frame count
    (the STL Reference Point Timecode, and the file package's start
    timecode), `duration` frames; a subtitle's essence, or with
    `captions` a captions' (RP 224's two kinds)."""
    rate = STL_RATES.get(bytes(stl[3:11]).decode('ascii', 'replace'), (25, 1))
    containers = [STL_CONTAINER]
    metadata = _Metadata(2, OP_1A, containers)
    descriptor = [('EssenceContainer', STL_CONTAINER), ('EventTextKind', STL_CAPTIONS_KIND if captions else STL_SUBTITLE_KIND),
                  ('EventTextLanguageCode', _utf16(language or 'und')),
                  ('STLReferencePointTimecode', struct.pack('>q', int(reference)))]
    header = metadata.build(STL_DESCRIPTOR, descriptor, [], rate, int(duration), 'STL Track',
                            int.from_bytes(GENERIC_STREAM_DATA[12:16], 'big'), timecode_start=int(reference),
                            timecode_base=int(round(rate[0] / rate[1])), package_name='EBU STL', body_sid=1, index_sid=0)
    streams = [(GENERIC_STREAM_PARTITION, 1, _klv(GENERIC_STREAM_DATA, bytes(stl)))]
    return _assemble(header, OP_1A, containers, 2, [], streams, None, index_in_footer=True)


# Reading.
def _read_ber(data, at):
    first = data[at]
    if first < 0x80:
        return first, at + 1
    size = first & 0x7f
    if not 0 < size <= 8 or at + 1 + size > len(data):
        raise ValueError('Not an MXF file: a KLV length cannot be read')
    return int.from_bytes(data[at + 1:at + 1 + size], 'big'), at + 1 + size


def packets(source, wanted=None):
    """(offset, key, value) of each KLV packet in `source` (bytes, or a
    file open for reading in binary), in order. A packet `wanted(key)`
    does not want is not read, and its value is None: a video's essence is
    gone over, not read. Raises ValueError where the packets stop making
    sense."""
    stream = io.BytesIO(bytes(source)) if isinstance(source, (bytes, bytearray, memoryview)) else source
    stream.seek(0, 2)
    end = stream.tell()
    at = 0
    while at + 17 <= end:
        stream.seek(at)
        head = stream.read(25)
        key = head[:16]
        if key[:4] != b'\x06\x0e\x2b\x34':
            raise ValueError(f'Not an MXF file: no KLV key at byte {at}')
        length, start = _read_ber(head, 16)
        if at + start + length > end:
            raise ValueError(f'The MXF file is cut short: a KLV at byte {at} runs past its end')
        value = None
        if wanted is None or wanted(key):
            stream.seek(at + start)
            value = stream.read(length)
        yield at, key, value
        at += start + length


def _partition_fields(value):
    (major, minor, kag, this, previous, footer, header_bytes, index_bytes, index_sid, body_offset,
     body_sid) = struct.unpack('>HHIQQQQQIQI', bytes(value[:64]))
    pattern = bytes(value[64:80])
    count, size = struct.unpack('>II', bytes(value[80:88])) if len(value) >= 88 else (0, 16)
    containers = [bytes(value[88 + i * size:88 + i * size + 16]) for i in range(count)]
    return {'minor': minor, 'this': this, 'footer': footer, 'header_bytes': header_bytes, 'index_sid': index_sid,
            'body_sid': body_sid, 'pattern': pattern, 'containers': containers}


def _is_partition(key):
    return key[:13] == _PARTITION[:13] and key[13] in (2, 3, 4)


def kind(head):
    """What the MXF whose first bytes are `head` carries for Subtitld:
    'timed-text' (ST 429-5), 'stl' (ST 2075) or None (a video, or not MXF
    at all), by the essence containers its header partition pack names."""
    head = bytes(head or b'')
    if len(head) < 120 or head[:13] != _PARTITION[:13] or head[13] != 2:
        return None
    try:
        length, start = _read_ber(head, 16)
        fields = _partition_fields(head[start:start + length])
    except (ValueError, struct.error, IndexError):
        return None
    containers = [_versionless(label) for label in fields['containers']]
    if _versionless(TIMED_TEXT_CONTAINER) in containers:
        return 'timed-text'
    if _versionless(STL_CONTAINER) in containers:
        return 'stl'
    return None


def looks_like(head):
    """Whether `head` (the first bytes) is an MXF Subtitld reads subtitles
    from."""
    return kind(head) is not None


def _decode(name, value):
    value = bytes(value)
    if name in ('ResourceID', 'AncillaryResourceID', 'InstanceUID'):
        return uuid.UUID(bytes=value[:16]) if len(value) >= 16 else None
    if name in ('UCSEncoding', 'NamespaceURI', 'MIMEMediaType', 'EventTextLanguageCode', 'TrackName', 'Name'):
        return value.decode('utf-16-be', 'replace').rstrip('\x00')
    if name in ('EssenceStreamID', 'BodySID', 'IndexSID', 'TrackID', 'LinkedTrackID', 'STLLineNumber'):
        return int.from_bytes(value, 'big')
    if name in ('ContainerDuration', 'STLReferencePointTimecode', 'StartTimecode', 'Duration'):
        return struct.unpack('>q', value[:8])[0] if len(value) >= 8 else 0
    if name in ('SampleRate', 'EditRate'):
        return struct.unpack('>ii', value[:8]) if len(value) >= 8 else (0, 1)
    if name in ('SubDescriptors',):
        count, size = struct.unpack('>II', value[:8])
        return [uuid.UUID(bytes=value[8 + i * size:8 + i * size + 16]) for i in range(count)]
    return value


# By label; EssenceStreamID's is BodySID's, and reads as BodySID.
_NAME_OF = {_versionless(ul): name for name, ul in _UL_OF.items() if name != 'EssenceStreamID'}


def _is_set(key):
    return key[:6] == b'\x06\x0e\x2b\x34\x02\x53' and key[8:13] == _SET[8:13]


def _is_wanted(key):
    """What structure() reads of a file: its partition packs, primer and
    sets, the timed text element and Generic Stream data."""
    return (_is_partition(key) or key == PRIMER or _is_set(key)
            or _versionless(key[:13]) == _versionless(TIMED_TEXT_ELEMENT[:13])
            or _versionless(key[:11]) == _versionless(GENERIC_STREAM_DATA[:11]))


def _open(source):
    """`source` as packets() takes it: bytes as they are, a path opened."""
    if isinstance(source, (bytes, bytearray, memoryview)):
        return bytes(source)
    return open(source, 'rb')


def structure(source):
    """What an MXF file holds (`source` its bytes, or its path):
    {'partitions': [fields], 'sets': [(kind, {property: value})],
    'essence': bytes of the timed text element or None, 'streams': {SID:
    bytes}, 'encrypted': bool}."""
    opened = _open(source)
    try:
        return _structure(opened)
    finally:
        if not isinstance(opened, bytes):
            opened.close()


def _structure(data):
    primer = {}
    sets, streams, partitions = [], {}, []
    essence = None
    encrypted = False
    current = None
    for _offset, key, value in packets(data, _is_wanted):
        if _is_partition(key):
            current = _partition_fields(value)
            current['generic_stream'] = key[13] == 3 and key[14] == 0x11
            partitions.append(current)
            continue
        if key == PRIMER:
            count, size = struct.unpack('>II', bytes(value[:8]))
            for number in range(count):
                entry = bytes(value[8 + number * size:8 + number * size + size])
                primer[int.from_bytes(entry[:2], 'big')] = entry[2:18]
            continue
        if _is_set(key):
            items, at, raw = {}, 0, bytes(value)
            while at + 4 <= len(raw):
                tag, size = struct.unpack('>HH', raw[at:at + 4])
                label = primer.get(tag)
                name = _NAME_OF.get(_versionless(label)) if label else None
                if name:
                    items[name] = _decode(name, raw[at + 4:at + 4 + size])
                at += 4 + size
            sets.append((key[14], items))
            continue
        if _versionless(key) == _versionless(ENCRYPTED_TRIPLET):
            encrypted = True
            continue
        if _versionless(key[:13]) == _versionless(TIMED_TEXT_ELEMENT[:13]):
            essence = bytes(value) if essence is None else essence
            continue
        if _versionless(key[:11]) == _versionless(GENERIC_STREAM_DATA[:11]) and current is not None \
                and current.get('generic_stream'):
            streams.setdefault(current['body_sid'], bytes(value))
    return {'partitions': partitions, 'sets': sets, 'essence': essence, 'streams': streams, 'encrypted': encrypted}


def read_timed_text(data):
    """The timed text track file in `data` (its bytes, or its path): {'document': the XML's bytes,
    'namespace', 'resource_id', 'rate' (numerator, denominator),
    'duration' (edit units), 'resources': [{'id' (UUID), 'type', 'data'}]}.
    Raises ValueError for a file that is not one, or is encrypted."""
    found = structure(data)
    if found['encrypted']:
        raise ValueError('This MXF is encrypted (SMPTE ST 429-6): it opens only with its key')
    descriptor = next((items for set_kind, items in found['sets'] if set_kind == TIMED_TEXT_DESCRIPTOR), None)
    if descriptor is None or found['essence'] is None:
        raise ValueError('This MXF holds no timed text (SMPTE ST 429-5)')
    by_instance = {items.get('InstanceUID'): items for set_kind, items in found['sets'] if set_kind == TIMED_TEXT_RESOURCE}
    resources = []
    for reference in descriptor.get('SubDescriptors') or []:
        sub = by_instance.get(reference)
        if not sub:
            continue
        resources.append({'id': sub.get('AncillaryResourceID'), 'type': sub.get('MIMEMediaType', ''),
                          'data': found['streams'].get(sub.get('BodySID'))})
    return {'document': found['essence'], 'namespace': descriptor.get('NamespaceURI', ''),
            'resource_id': descriptor.get('ResourceID'), 'rate': descriptor.get('SampleRate', (24, 1)),
            'duration': descriptor.get('ContainerDuration', 0), 'resources': resources}


def read_stl(data):
    """The EBU STL in the MXF `data` (its bytes, or its path): {'stl': its bytes, 'language' (the
    descriptor's tag, or ''), 'reference' (the STL Reference Point
    Timecode, frames), 'captions' (its kind is captions)}. Raises
    ValueError where there is none."""
    found = structure(data)
    if found['encrypted']:
        raise ValueError('This MXF is encrypted: it opens only with its key')
    descriptor = next((items for set_kind, items in found['sets'] if set_kind == STL_DESCRIPTOR), {})
    linked = [items.get('BodySID') for set_kind, items in found['sets'] if set_kind == CONTAINER_DATA]
    stl = None
    for sid in linked + sorted(found['streams']):
        candidate = found['streams'].get(sid)
        if candidate and candidate[3:11].decode('ascii', 'replace') in STL_RATES:
            stl = candidate
            break
    if stl is None:
        raise ValueError('This MXF holds no EBU STL (SMPTE ST 2075)')
    kind_label = descriptor.get('EventTextKind') or b''
    return {'stl': stl, 'language': descriptor.get('EventTextLanguageCode', ''),
            'reference': descriptor.get('STLReferencePointTimecode', 0),
            'captions': _versionless(kind_label) == _versionless(STL_CAPTIONS_KIND)}
