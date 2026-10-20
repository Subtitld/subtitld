# Live co-editing: plan

Several people work on the same Subtitld project at the same time. The
subtitld.cc server coordinates the work, and media goes directly between
collaborators.

Branch `live-collaboration` in both repositories:

- `subtitld` (desktop app)
- `subtitld.cc` (server)

## Decisions

| Topic | Decision |
|---|---|
| Who can join | Only signed-in subtitld.cc users with an edit grant on the project. No "anyone with the link" mode. |
| Scope | Subtitle lines, timing, speakers, translations and dubbing. |
| Offline | Offline editing is supported. Changes are merged on reconnect. |
| Media storage | No media ever goes to the server or to R2. The server only stores metadata. |
| Media transfer | Peer-to-peer between collaborators, encrypted end to end. |
| Media quality | The original files, byte for byte. Lossless compression during transfer is fine. A lower-quality proxy option comes in a later phase. |
| PyAV | Not used. Data channels only (libdatachannel, or aiortc's data-channel parts without its media stack). |

## Starting point

### subtitld.cc

What exists:

- Django 5.2 behind gunicorn (WSGI only), on the CloudPanel VPS behind Cloudflare.
  The draft environment runs on port 8121 and production on 8122.
- PostgreSQL. Redis, with database 7 used for the cache.
- Accounts, `Principal`, groups with `Membership` and `Invitation`.
- `ApiToken` per app installation, with scopes (`read publish rate`). The app
  signs in with the PKCE loopback or device-code flow.
- `Asset`, immutable `Version` with a parent, and `Grant` (view or edit).
  Publishing conflicts already return 409.
- Video fingerprints (`VideoFile`, `VideoMatch`). Videos are never uploaded.
- `PLAN.md` Release 3 already lists drafts, collaborative translation, locks
  and credits. Release 2 lists line-anchored comments.

What's missing:

- Anything realtime: ASGI, Channels, websockets.

### Desktop app

- State lives in module-level dicts in `modules/session.py` (`SUBTITLE`,
  `SPEAKERS`, `VIDEO`). Each line is a plain dict.
- **Lines have no stable IDs.** Code finds them with `segments.index(dict)`.
  Only dub clips have a `uid`.
- **Undo copies the whole document** before each edit (`modules/history.py`,
  up to 100 copies).
- Most edits go through `modules/subtitles.py`, but interface code also
  changes state directly. Change notification is `session.set_unsaved()`,
  called from about 100 places.
- Networking is blocking `urllib` on QThreads. There is no QtNetwork or
  websocket code yet.
- Media is referenced by local path. Derived files (waveform, stems, proxy,
  dubs) sit in the cache, named by `utils.get_cache_key(video_path)`.
- The project format is `.usfx` (a ZIP with USF, a manifest and assets).

## Architecture

### Overview

- **The server holds the official copy of the project.** It keeps a log of
  changes, numbering each change in the order it arrives.
- **Each app keeps a full local copy.** It applies its own changes right away,
  sends them to the server, and applies everyone else's changes as they arrive.
- **The server also matchmakes for media.** It passes WebRTC connection-setup
  messages ("signaling") between collaborators and keeps a list of which
  online collaborator has which file. Media bytes never pass through it,
  except as encrypted traffic through an optional TURN relay.

### Document model

Lines are a **set keyed by stable ID**, not a list. Their order always comes
from start time, so no list CRDT is needed.

Change types:

| Change | Meaning |
|---|---|
| `cue.insert {id, start, end, text, speaker, …}` | Add a line |
| `cue.delete {id}` | Remove a line |
| `cue.set {id, field, value}` | Set one field: `start`, `end`, `text`, `speaker`, `translations.<lang>`, `locked` |
| `speaker.set {name, …}` / `speaker.delete {name}` | Speaker color, image hash, dubbing voice settings |
| `dub.set {cue_id, uid, hash, voice, rate, pitch, …}` / `dub.delete {uid}` | Dub clip records |
| `meta.set {field, value}` | Language, source language, voice mix |
| `media.add {hash, kind, size, chunks, fingerprint}` | A media file in the project's media list |

- **Combined changes.** Split, merge and "send text to next line" are each one
  combined change (several of the changes above together). The server applies
  all of it or none of it.
- **Revisions.** Every line carries a revision number, which goes up on each
  change. A change says which revision it was based on.

### Conflict rules

Online:

- **Timing, speaker, locked and translations:** the latest change wins, one
  field at a time, in server order.
- **Text uses soft locks.** When someone starts editing a line's text, the
  others see "Maria is editing" and that text is read-only for them. The lock
  frees itself after a few seconds of inactivity or when the person
  disconnects.
- **Combined changes based on an old revision are refused.** The client
  reloads the affected lines and shows a short notice.

Offline:

- **Locks don't apply.** Each queued change keeps the revision it was based on.
- **On reconnect:**
  - Changes to lines nobody else touched apply directly.
  - Timing and speaker changes use "latest wins", one field at a time.
  - If two people changed the same line's text, the app keeps both versions and
    shows the line in a conflict list with "keep mine / keep theirs / edit".
  - A split or merge on a line someone else deleted also goes to the conflict
    list.
- **Where the queue lives.** Queued changes are saved with the project, so
  they survive restarts.
- **Possible upgrade:** if text conflicts turn out to be common, switch only
  the text field to a CRDT text type (pycrdt / Yjs).

### Presence

Each person gets a color and an avatar. The app shows which line they have
selected, where their playhead is and which text they're editing. Presence
updates go to the others but aren't saved.

### Protocol (JSON over websocket)

| Direction | Message | Purpose |
|---|---|---|
| app → server | `hello {doc, last_seq, device}` | Join, or rejoin after a disconnect |
| server → app | `snapshot {seq, state}` or `ops {from_seq, ops[]}` | Initial state, or the changes since `last_seq` |
| app → server | `op {client_op_id, base_seq, ops[]}` | Send a local change |
| server → sender | `ack {client_op_id, seq}` / `reject {client_op_id, reason, cues}` | Confirm or refuse it |
| server → others | `op {seq, author, ops[]}` | Pass it on |
| both | `presence {...}` | Selection, playhead, editing state |
| both | `lock {cue_id}` / `unlock {cue_id}` | Soft text locks |
| both | `rtc {to, payload}` | WebRTC connection setup between two collaborators |
| both | `have {hashes[]}` / `want {hash}` | Which media files each collaborator has or wants |
| both | `ping` / `pong` | Every 30 s. Cloudflare closes connections idle for about 100 s. |

## Server (subtitld.cc)

### Service

- New ASGI app (Django Channels) served by uvicorn, as its own systemd unit.
  Ports 8123 for draft and 8124 for production.
- nginx forwards `/ws/` to it with the websocket upgrade headers. All other
  traffic stays on gunicorn.
- The Channels message layer uses Redis, on its own database (not 7).

### Data

| Model | Purpose |
|---|---|
| `CollabDoc` | The shared project. Links to an `Asset`, or to the Release 3 `Draft`. Has the current `seq`. |
| `CollabOp` | `seq`, author, device, `client_op_id`, payload, created_at |
| `CollabSnapshot` | Full state every N changes, so joining doesn't replay the whole log |
| `CollabMedia` | Media list entry: hash, kind, size, chunk hashes, fingerprint. **Metadata only.** |
| `CollabMediaCopy` | Who received which media file, and when. Audit for studios. |

### Permissions

- Edit grant (`Grant`) means "can co-edit". Inviting people uses the existing
  groups and invitations.
- New token scope `collab`. The app asks for it once, through the existing
  sign-in flow.
- Media connection-setup messages only pass between members of the same
  `CollabDoc` who are online.

### Publishing

"Publish" turns the current state into a normal immutable `Version`. Credits
are drawn from the change authors (`DraftContribution` in `PLAN.md`).

### TURN relay

Optional. Either coturn on the VPS with a bandwidth cap, or a hosted TURN
service. It's off by default for original media; small files (dubs, waveform)
may use it.

## Desktop app

### Phase 0 groundwork

This work is useful even without co-editing.

1. **Stable line IDs** (done: `modules/cue_ids.py`).
   - Add `id` to every segment dict and save it in USF.
   - Assign IDs when opening files that don't have them.
   - Use the ID for selection everywhere, instead of object identity or
     position. Undo does this now; the rest of the app moves over as it is
     touched.
2. **Every edit recorded as changes** (done: `modules/docops.py`,
   `modules/journal.py`).
   - Instead of routing roughly 100 edit sites through a command layer, a
     journal keeps a clean copy of the document. At every `set_unsaved()` and
     `history_append()` it compares the live state with that copy and turns the
     difference into the changes listed under "Document model". That covers
     every edit path, including ones nobody would remember to port.
   - `docops` is pure Python: `apply` (which returns the undoing changes),
     `diff` and `snapshot`. The server will use the same module.
   - Opening a project is not an edit: the open flows call
     `session.document_loaded()`.
   - Explicit commands can still be added later where the intent matters.
3. **Change-based undo** (done: `modules/history.py`).
   - Each batch of changes comes with its inverse. Undo applies the inverse as
     a new change.
   - A step changes only what it changed: edits made since by anything else
     stay. The recorder's late transcripts go into the step that created
     their line (`history.amending()`), or into the stored copies when the
     line isn't live (`history.amend_stored()`).
   - Runtime data (the recorder's `_rec`, speaker pictures) is not part of the
     document; the journal keeps the latest per line and speaker, and undo puts
     it back.
   - This replaces the full-document copies in `history.py`. It also makes undo
     cheaper and keeps working with other people's edits in between.
4. **Local change queue** (done: `modules/collab_state.py`). While a project
   is shared, every recorded batch is queued with an ID and the server change
   number it was made on top of. The shared document's ID, that number and
   the queue are saved in the `.usfx` (`collab.json`, `pending.jsonl`) and
   come back when it is opened. This is the base for offline editing.

### Co-editing client

- Websocket connection on a QThread, or with `QWebSocket` from QtWebSockets.
  Check that QtWebSockets is included in every package format.
- Reconnect with backoff. Catch up from `last_seq`, then send the queued
  changes.
- **A separate path for changes from others.** It updates the session and
  refreshes the views, but adds no undo steps and keeps the user's selection.
- Conflict list panel for offline merges.
- Interface:
  - a "Share live" dialog (invite and manage members);
  - avatars in the top bar;
  - colored outlines on lines and on the timeline;
  - a lock badge on lines being edited;
  - connection status;
  - a "Publish to subtitld.cc" action.

### Media sharing (peer-to-peer)

**What travels:**

| File | When | Notes |
|---|---|---|
| Original video | On request | Byte for byte. Usually GBs. |
| Waveform `.npy` | On request, with the video | Small |
| Vocals/background stems | On request | Already FLAC. Slow to compute, so worth sending. |
| Dub clips | **Automatically** | Small. Needed for playback and export. |
| Speaker images | Automatically | Small |
| Scene list | As ordinary data | Tiny; goes through the change log as data |
| Proxy video | Later phase | Lower-quality option |

**Transport:**

- WebRTC data channels, with ICE to find a route and DTLS for encryption.
- **No PyAV.** Evaluate libdatachannel (C++, fast) against aiortc's ICE, DTLS
  and SCTP parts without its media modules. Speed matters for multi-GB
  originals, so libdatachannel is the likely choice. QUIC with our own
  hole-punching is the fallback.
- Same-network connections are found automatically and go at full local
  network speed.

**Transfer:**

- The content hash covers the whole file, with chunk hashes for chunks of
  about 4 MB.
- Chunks are fetched from every collaborator who has the file, in parallel.
  Each chunk's hash is checked on arrival.
- Resumes after a disconnect or an app restart.
- **Lossless compression per chunk.** Compress with zstd and keep it only if
  it's clearly smaller. WAV dubs travel as FLAC and are turned back into the
  exact original audio on arrival.
- **No re-encoding** (FFV1 or similar), because the file must stay identical
  to the original.
- The finished file must match the project's fingerprint before the app uses
  it.

**User controls:**

- "Share my media with collaborators" per project, off by default.
- Choose where downloaded media goes. The project relinks the video to that
  local path; each person's path is local and never syncs.
- Check free disk space before starting. Bandwidth limit, pause and resume.
- Status messages such as "Available from Maria (online)" or "No one with this
  file is online".

**When no one with a dub clip is online:**

- The line shows "Dub by Ana, waiting for a collaborator".
- "Generate locally" is offered, using the synced voice settings. The result is
  a new clip.
- Export warns about missing dubs.

**Cache:** files that arrive go into the existing cache under the same names
(`get_cache_key`), so the rest of the app treats them as local results.

## Phases

| Phase | Repo | Scope |
|---|---|---|
| 0 | app | Stable line IDs, changes recorded by the journal, change-based undo, saved change queue. Ships on its own. |
| 1 | server | ASGI/Channels service, `CollabDoc`/`CollabOp`/`CollabSnapshot`/`CollabMedia`, `collab` scope, nginx and systemd on draft, media connection setup, TURN decision |
| 2 | app + server | First working version: line, timing, speaker and translation changes, presence, soft locks, offline merge with the conflict list, peer-to-peer transfer of originals, waveform, stems and dubs, fingerprint check, publish to a Version, audit of media copies |
| 3 | app + server | Proxy option, playhead-first fetching, comments on lines, change history and credits view, CRDT text if conflicts are common |

## Risks and open questions

- **Packaging.**
  - libdatachannel (or the trimmed aiortc parts) and QtWebSockets need to work
    in Flatpak, snap, AppImage, MSIX, macOS and Haiku.
  - Test packages locally first, on a real desktop session.
- **Routers and firewalls.** Strict networks block direct connections.
  Originals then can't move without a relay. The proxy phase is the answer
  there.
- **Size of the Phase 0 refactor.** Around 100 places call `set_unsaved()`, and
  undo is used everywhere. This is the biggest piece of work and the riskiest.
- **Dub regeneration isn't bit-exact.** Clips generated locally are new
  versions, not copies.
- **Copyright and confidentiality.** Sharing is opt-in, limited to project
  members and encrypted. Each received copy is recorded on the server.
- **Copies of a shared project.** "Save as" and autosave backups carry the
  shared document's ID and queue. Opening two copies could send the same
  changes twice (the server drops repeated batch IDs) or diverging ones.
  Decide in M9/M10 whether a copy stays linked.
- **Server load.** One uvicorn process should handle many projects. Measure on
  draft before production.
