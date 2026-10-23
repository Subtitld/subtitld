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

Done in M6 (`collab/consumers.py`, at `/ws/collab/<doc_id>`): `hello`, `welcome`,
`snapshot`/`changes`, `op`, `ack`/`reject`, `batch`, `presence`, `left`,
`lock`/`locked`/`unlocked`, `ping`/`pong`, `error`. The connection needs the
`Subtitld/` user agent and a bearer token with the `collab` scope; a browser
(any `Origin` header) is refused at the handshake. Refusals close with 4400
(user agent), 4401 (sign in), 4403 (scope) or 4404 (no such project, or not
yours). A lock lasts 15 s unless asked for again. Batches and acks can
overtake each other: the app applies them by `seq`. `rtc`, `have` and `want`
come with the media work (M13).

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

Done in M5: the `collab` app (`collab/models.py`, `collab/access.py`,
`collab/documents.py`) and the shared package `packages/collab`
(`subtitld-collab`), the home of `docops`; Subtitld bundles a copy made with
`packages/collab/copy_into_subtitld.py`.

| Model | Purpose |
|---|---|
| `CollabDoc` | The shared project: owner (user or group), title, status, the current `seq`, and the `Asset` it publishes to. Not an `Asset` itself: one with no version would show in libraries and search as an empty subtitle. |
| `CollabMember` | A user or group the owner shared it with. |
| `CollabOp` | `seq`, author, `client_op_id` (unique per project: a batch sent twice is accepted once), `base_seq`, the changes |
| `CollabSnapshot` | Full state every N changes, so joining doesn't replay the whole log |
| `CollabMedia` | Media list entry: hash, kind, size, chunk hashes, fingerprint. **Metadata only.** |
| `CollabMediaCopy` | Who received which media file, and when. Audit for studios. |

### Permissions

- Co-editing follows `assets.access.can_edit`: the owner, the owning group's
  members and the `CollabMember` grantees, with an active account. Sharing
  and closing: the owner, or an owner of the owning group.
- Accepting a batch locks the project's row, gives it the next `seq` and
  refuses it whole when its changes no longer fit (`documents.accept`).
- Deleting an account deletes the shared projects it owns; what it wrote in
  other people's projects stays.
- New token scope `collab`. The app asks for it once, through the existing
  sign-in flow.
- Media connection-setup messages only pass between members of the same
  `CollabDoc` who are online.

### REST endpoints

Done in M7 (`api/routers/collab.py`, `collab/sharing.py`), under
`/api/v1/collab/projects`, with the `collab` scope:

| Endpoint | What |
|---|---|
| `POST /collab/projects` | Share a project: title, owner (yourself or a group you may publish as), the document. Idempotency-Key. |
| `GET /collab/projects` | The ones you co-edit, most recently changed first (cursor pages). |
| `GET /collab/projects/{doc_id}` | One, with its members, websocket address and whether you manage it. |
| `PATCH /collab/projects/{doc_id}` | Rename (owner). |
| `DELETE /collab/projects/{doc_id}` | Close (owner). |
| `POST /collab/projects/{doc_id}/members` | Share with a person or group by handle (owner). |
| `DELETE /collab/projects/{doc_id}/members/{handle}` | Remove someone (owner), or leave (your own handle). |

Removing someone or closing the project tells its open connections to check
access again, so whoever lost it is disconnected at once (needs the gunicorn
processes on the same Redis channel layer as uvicorn). No inbox notification
yet when a project is shared with you; the app's shared projects list shows it.

### Publishing

"Publish" turns the current state into a normal immutable `Version`. Credits
are drawn from the change authors (`DraftContribution` in `PLAN.md`).

Done in M12: publishing uses Subtitld's Publish card as before (the shared
project's document is in the editor). After publishing from a shared project
the app calls `POST /collab/projects/{doc_id}/published`: the project then
publishes to that subtitle (`CollabDoc.asset`; another one is refused,
`different_subtitle`), and `CollabPublication` credits everyone whose changes
the project had. The subtitle's versions show "with @…", and the API's
versions carry `coeditors`. Project details include the `subtitle` it
publishes to; each collaborator's editor takes it as its origin, so the
Publish card offers its next version to those who may edit it (membership
in the project does not grant that). Client package 1.3.0
(`project_published`).

### Deploy

Prepared in M8 (`DEPLOY.md` → Live co-editing): `ops/subtitldcc-{draft,prod}-collab.service` (uvicorn,
one process, ports 8123 and 8124, pings every 20 s, no access log), `ops/nginx-collab.conf` (the `/ws/`
location), sudoers lines, and CI writing `CHANNELS_REDIS_URL` (Redis database 8), restarting the service
and checking the websocket upgrade. Reproduced locally with nginx + TLS in front of gunicorn and uvicorn
under production settings: that caught redis-py 8's 5 s socket timeout cutting channels_redis's 5 s
blocking reads (every quiet connection crashed), now raised to 30 s and covered by a test on a real Redis
in CI. The one-time server setup is still to do.

### TURN relay

Optional. Either coturn on the VPS with a bandwidth cap, or a hosted TURN
service. It's off by default for original media; small files (dubs, waveform)
may use it.

**Prepared (2026-10-22), not deployed.** The decision waits on tests across
real networks.

- subtitld.cc: `COLLAB_TURN_SECRET`, `COLLAB_TURN_URLS` (comma-separated)
  and `COLLAB_TURN_TTL` (24 h). `collab.turn` makes coturn's shared-secret
  credentials (`use-auth-secret`): the username is `<expiry>:<principal id>`,
  the password a base64 HMAC-SHA1 of it with the secret. Nothing is stored.
  The websocket's `welcome` carries them as `ice` (`[]` without a relay).
- Subtitld keeps `ice` from the welcome (`collab_sync`). The transfer manager
  adds the relays to its STUN server, credentials percent-encoded into the
  URL (`turn:user:pass@host:port`), which libdatachannel decodes. ICE uses
  the relay only when a direct link fails.
- **UDP only.** libjuice, which every build uses, relays over UDP: no TURN
  over TCP or TLS, so a network that blocks all UDP still gets no media.
  `turns:` and `transport=tcp` URLs are skipped.
- Tested with coturn 4.6.3 in Docker (`use-auth-secret`): credentials from
  `collab.turn` accepted, a relay-only link (its only candidate `relay`)
  carried 4 MB intact.
- On the VPS it would be: coturn on UDP 3478, a small relay port range, a
  bandwidth cap, and the secret in both coturn's config and `app.env`.

**Found while testing:** a process that had used a link never ended.
libdatachannel's cleanup ran while Python finalized, holding the GIL a last
callback waited for. `rtc.py` now cleans up at exit (`atexit`, before
finalization); test_rtc checks a process ends.

**This network's NAT (2026-10-22):** endpoint-independent mapping (one local
port reached two STUN servers as the same outside address and port), so
links from here need only STUN. The hard case left to test is a symmetric
NAT (mobile data, many corporate networks) on the other side.

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

Done in M9 (`modules/collab_sync.py`, `modules/collab_client.py`):

- The app keeps the document as of the last change number it has (`base`,
  saved in the `.usfx` as `collab-base.json`) and its unconfirmed batches.
  What the person sees is the base with those batches on top. A
  collaborator's batch goes into the base; the screen is rebuilt from it,
  and only what differs is changed, as changes from the server (not
  undoable, not sent back). Batches apply in number order; a lasting gap
  means saying hello again.
- A batch the server refuses leaves the screen and is listed. Words both
  sides changed while this app's change was waiting are listed as conflicts;
  this app's text stays (it reaches the server later).
- Undo here no longer reverts a field a collaborator changed since
  (`history.forget`).
- `Connection` (QWebSocket): reconnects with growing waits up to a minute,
  pings every 25 s, stops for good on 4400/4401/4403/4404. The app now asks
  for the `collab` scope when signing in.
- Tested with a simulated server and three collaborators whose messages
  arrive late, out of order or not at all (300 runs converge), and against a
  local uvicorn with the real session: edits both ways, undo, a dropped
  connection with an edit made offline.

Done in M10 (`modules/collab_service.py`, `interface/subtitldcc_live.py`, a
"Live co-editing" card in the subtitld.cc panel; client package 1.2.0 with
`projects`, `share_project`, `add_member`, `remove_member`, `close_project`):

- Share the open project as yourself or a group you publish as; edits made
  while subtitld.cc was taking it are queued as the first changes.
- Open a project shared with you: it takes the editor's place (like opening
  a subtitld.cc subtitle), fills in as the connection catches up, and saving
  asks where to put it. Each person keeps their own video open.
- A shared project connects when it is opened (`session.document_loaded`
  callbacks) and disconnects when another one is, or on signing out.
- Shared: connected / connecting / offline with how many changes wait, who
  is here, the members (the owner adds and removes them), conflicts, and
  "Sign in again" for a token from before co-editing. Leave, or stop sharing
  (owner); either way the project stays here as one's own. A project no
  longer shared with you (4404) becomes your own too.
- English texts only for now, like other recent features.

Done in M11:

- Presence: every 0.5 s, when it changed, the app says which subtitle is
  selected, where the playhead is and which text is being typed in; it says
  it again after reconnecting. Each collaborator has a colour picked by
  handle. The timeline shows their selection (dashed outline), the subtitle
  they type in (a "✎ @handle" tag) and their playhead (`Timeline._paint_collaborators`).
- Text locks: typing in the subtitle list's editor holds the subtitle's lock
  (asked again every 5 s, given up after 10 s without a keystroke or when
  another subtitle is selected). A subtitle someone else is typing in is
  read-only in the editor, with a note saying who.
- Batches never sent go as one, so typing a word is one change to send. Only
  never-sent batches merge: one that may have reached the server keeps its
  ID (the convergence test caught the case).
- Conflicts in the card: "Keep mine" or "Use theirs" (an undoable edit,
  sent like any other); a refused batch is shown and dismissed.
- Collaborators' changes refresh the timeline and the panels (at most every
  150 ms).

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

**Done in M13 (the media list):** the list is not in the document (a 10 GB
original's chunk hashes alone are ~160 KB of JSON, in every snapshot): it is
`CollabMedia` on the server, registered over REST
(`/collab/projects/{doc_id}/media`, idempotent by hash; names are file names,
never paths; a stem's name is its role) and announced to everyone connected
(`media`). On the websocket, `have {hashes}` says what an app can send (told
to everyone, and to whoever joins) and `rtc {to, payload}` carries set-up for
a direct link to one connection only. In the app, `modules/collab_media.py`
describes files (SHA-256 whole and per 4 MiB chunk) in the background, once
(an index by path, size and modification time), finds the project's video,
waveform and stems, and knows where a received waveform or stem goes (under
the key of the video open here). Once live, a shared project registers what
is missing from the list and sends `have`. Client package 1.4.0.

**Transport spike (2026-10-20).** Two processes on one machine, 256 MB
(loopback) or 32 MB (40 ms round trip and loss, `tc netem` inside a
container). Single runs, so allow some noise:

| | Loopback | 40 ms | 40 ms, 0.05% loss | 40 ms, 0.5% loss |
|---|---|---|---|---|
| libdatachannel via ctypes, tuned | 85–111 MB/s | 14–62 MB/s | 3.0 MB/s | 0.4 MB/s |
| aioice + aioquic, CUBIC | 25–27 MB/s | 13–19 MB/s | 2.0–3.3 MB/s | 0.5 MB/s |
| aiortc data channel (needs PyAV) | 28 MB/s | 11 MB/s | 4.8 MB/s | 2.4 MB/s |

- On a local network, libdatachannel is 3–4 times faster: the Python
  options are CPU-bound at about 25–30 MB/s.
- Over the internet, loss and congestion control decide the speed, not the
  library. aiortc's higher numbers there most likely mean its congestion
  control backs off less than the standard says it should.
- libdatachannel's defaults are poor with loss. Tuned: default congestion
  control, 8 MB buffers, 100 ms minimum retransmission timeout, delayed
  acknowledgements off (0.6 → 2.5–3.0 MB/s at 0.05% loss). H-TCP is broken
  under loss (0.1–0.2 MB/s).
- Parallel connections fix loss-limited links: four libdatachannel
  connections moved 32 MB at about 24 MB/s together at 0.05% loss, against
  3.0 for one (short transfers, so somewhat flattering). Downloading chunks
  from several collaborators at once gives this anyway.
- `pylibdatachannel` (Linux wheels only) and `datachannel` (abandoned) are
  not usable; libdatachannel would need our own ctypes bindings to its C
  API. It is MPL-2.0 and 3.2 MB built without media or websocket, and
  needs OpenSSL.
- aiortc imports PyAV unconditionally. libdatachannel and aiortc both speak
  standard WebRTC, so they can talk to each other; aioquic cannot talk to
  either.
- Not tested: a real router-to-router connection (needs a second network),
  and packaging on Windows, macOS and Haiku.

Scripts: `~/.cache/collab-spike/bench_*.py`.

**Decision (2026-10-21): libdatachannel**, through our own ctypes bindings to
its C API, with the tuned SCTP settings above and several connections at
once (chunks from several collaborators, and more than one connection per
collaborator on lossy links). It needs a native build in every package; if
Haiku can't build it, an aiortc-based fallback (vendored data-channel parts,
no PyAV) can still talk to it.

**Done in M14 (the transfer engine):**

- `packaging/libdatachannel/build-linux.sh` builds libdatachannel v0.23.2 in
  manylinux_2_28 (glibc 2.25 at most), data channels only, with Mbed TLS
  3.6.4 linked in (DTLS-SRTP turned on; its x509 and crypto archives added
  to the link line), so it needs no OpenSSL. 64.7 MB/s on loopback.
- `modules/rtc.py`: ctypes bindings, one callback of each kind dispatching
  by ID; messages that arrive before a handler is set are kept for it. SCTP
  keeps only "no delayed acknowledgements" from the spike (2.6 MB/s at
  0.05% loss): 8 MB buffers or a short retransmission timeout each stalled
  links now and then once several shared a process.
- `modules/collab_transfer.py`: set-up through `rtc` messages (candidates
  that arrive before their description wait for it); JSON requests and
  binary chunk frames; chunks from everyone who has the file, three at a
  time each, checked against the media list's hashes, written into a
  `.part` file with a progress record for resuming; the whole file checked
  at the end; zlib for compressible kinds. Nothing calls libdatachannel from
  inside its callbacks (a send from a message callback could stop a link).
  A refusal is remembered for 60 s.
- Sharing is opt-in per project ("Share my media with collaborators",
  saved in `collab.json`): off, nothing is announced nor sent. Received files
  join the index and are passed on; subtitld.cc notes the copy and who sent
  most of it. The waveform and stems are offered when the video open here is
  the project's (fingerprint), and go where Subtitld looks for them; an
  original goes to a folder the person picks, then "Use this video".
- Tested: two managers in one process over real links (one source, two at
  once, compressed, refused, resumed; 20 runs clean), and two Subtitld
  processes against a local server (40 MB in 2.1 s, identical, copy noted;
  with sharing off the video stays unavailable).
- Not done: a bandwidth limit and pause, a TURN relay, a router-to-router
  test, builds for Windows, macOS and Haiku.

**Done in M15 (dub clips):**

- A dub's files are this computer's: the shared document leaves out `path`
  and `raw_path` at any depth (`docops.LOCAL_KEYS`) and names each file by
  the SHA-256 of its bytes (`hash`, `raw_hash`), for the dub and each of its
  subclips. `collab_media.stamp_dubs()` runs before every recorded change and
  on load (`session._before_capture_callbacks`); the index remembers each
  hash, so a file is hashed once.
- `collab_media.resolve_dubs()` finds the local copy of each hash (the index,
  or `cache/dubbing/<hash>.<ext>`) after a collaborator's changes and after
  undo or redo, and drops the path of those not here.
- Dub clips go to every collaborator whatever "Share my media" says (they
  are project content, not someone's original): each Subtitld lists its
  clips as media of kind `dub`, and fetches the missing ones, four at a
  time, into the dubbing cache. The panel says "Receiving N dub clips" or
  "N dub clips waiting for a collaborator"; audio export warns when some
  are missing, and the player picks received clips up at once.
- Tested: undo and redo keep dub files (new take, subclips, a deleted line
  coming back); two Subtitld processes against a local server, with sharing
  off: the second received the three clips 3.2 s after joining, identical,
  subclips resolved, paths local.
- Not done: speakers' pictures, "Generate locally" for a waiting clip, FLAC
  for WAV clips on the way.

**M16 (packaging), in progress:**

- libdatachannel v0.23.2, data channels only, in every package:
  - AppImage and the PyInstaller builds: `packaging/libdatachannel/build-linux.sh`
    (Docker, manylinux_2_28, Mbed TLS), `build-windows.ps1` (MSVC, Mbed TLS,
    static C runtime), `build-macos.sh` (arm64, Mbed TLS); the specs bundle
    the library next to the app with its licences, and CI caches each build
    by its script.
  - snap: a `libdatachannel` part (CMake, the base's OpenSSL), found through
    `SUBTITLDCC_LIBDATACHANNEL`.
  - Flatpak: a `libdatachannel` module (CMake, the runtime's OpenSSL);
    `find_library` finds it in /app/lib.
  - AUR: Arch's `libdatachannel` (0.24.5; its C API is the same for what
    rtc.py uses) and `qt6-websockets`.
  - Haiku: HaikuPorts' libdatachannel (0.24.5) cannot open a link there: it
    uses libnice, which cannot list the network interfaces (SIOCGIFCONF
    fails), and the system usrsctp is built with the Linux `sockaddr_conn`
    while Haiku's sockaddr starts with `sa_len` (bind fails: EAFNOSUPPORT,
    then EADDRNOTAVAIL). The Subtitld recipe builds its own, with libjuice
    and the bundled usrsctp (`usrsctp-haiku.patch`: HaikuPorts'
    libtorrent fixes plus the BSD layout, `HAVE_SCONN_LEN`), into
    `subtitld/lib`, where rtc.py looks. libjuice's dual-stack socket sends
    to IPv4 peers as v4-mapped addresses, which Haiku refuses (ENETUNREACH):
    rtc.py binds IPv4 there. test_rtc (32 MB) passes in the VM.
  - pip: no library; the panel says media can't be sent ("media_no_links").
  - rtc.py also tries `libdatachannel.so.0.24` / `.0.23` by name (a system
    library without the development package's link).
- libjuice draws ICE credentials from the system's random source only on
  Linux and Windows; elsewhere from `random()`/`rand()` seeded with the
  clock. `libjuice-urandom.patch` reads /dev/urandom (macOS, Haiku builds).
  Worth sending upstream.
- QtWebSockets: in the PyInstaller hidden imports; the AppImage smoke test
  checks it and libdatachannel are in and resolve on a bare system.
- `.github/scripts/collab-check.py`: Qt websockets plus 4 MB across a link,
  with a package's own Python; run by the snap, Flatpak, AUR and Haiku
  checks.
- MSIX: a firewall rule for inbound UDP, so Windows doesn't ask (and a
  "Cancel" doesn't block links on the local network).
- Tested locally: AppImage (clean Ubuntu 24.04 and Fedora, and opened on a
  real Wayland session), AUR (Arch's library, 4 MB intact), Flatpak
  (installed bundle: window on a real Wayland session, check passes),
  Haiku (haiku-build.sh in the VM: packages built and installed, check
  passes with the bundled library, window on Haiku's desktop). In CI
  (2026-10-21): Windows (DLL in the flat bundle, window, zip, installer,
  MSIX with the firewall rule as a package extension), macOS (dylib in
  the bundle, nothing from Homebrew), snap (check passes on a clean
  Ubuntu: library at $SNAP/usr/lib, 4 MB intact).
- subtitld.cc's deploy restarts the co-editing service only when its unit
  is enabled (`systemctl is-enabled`: the unit file is unreadable to the
  deploy user, so `systemctl cat` never found it). Draft runs M9-M16.
- Left: a real-session test with two collaborators on draft (two people,
  two accounts), then the beta.

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

## Editors' chat (text and voice)

Planned 2026-10-22. Decisions by the user:

- **No chat history on subtitld.cc.** The server passes messages on and keeps
  none. Each Subtitld keeps the conversation; whoever wants to can save it in
  the project (`.usfx`, `chat.json`), an option per project.
- **Push-to-talk is the default** for voice; open mic is an option.
- **No limit on people in a voice call**, but a subtle note when there are
  many (each person links directly to every other).

### Text

- A `chat` websocket message: `{id, text, cue_id?, at_ms?, mentions?}` from
  the app; the server adds the author and time and passes it to the others in
  the project, stores nothing, and checks only length (2000 characters) and
  rate.
- **Catching up without a server copy.** Someone who joins (or comes back
  online) asks the others for what they missed: a `chat_history` request goes
  to one collaborator who is online, who answers that person directly
  (relayed like `rtc`). Messages are kept by `id`, so copies from two people
  merge. When no one who was there is online, what was said while you were
  away is not there to get; the panel says so.
- Messages written offline wait like edits and go out on reconnect.
- A message can point at a line or a time: clicking it selects the line and
  moves the playhead there. `@mentions`, an unread badge on the panel's
  button, edits and deletions of your own messages (as messages, so everyone
  applies them).
- Saving in the project: off by default; when on, `chat.json` in the `.usfx`
  holds the conversation as this person has it, and opening the project brings
  it back.
- Moderation: nothing stored means nothing to report on the server; a
  "Remove from the project" for the owner stays as it is.

**Text chat done (2026-10-22):**

- subtitld.cc (`collab.chat`): `chat` stamps the author and the time and
  signs the message (Django's Signer, salted with the project, over the
  signed fields as sorted JSON); `chat_history_request` and `chat_history`
  are addressed to one connection, like `rtc`, and every message of a copy
  is checked on the way: an altered or invented one is dropped. 2000
  characters, 20 messages in 10 s per connection, copies up to 500 messages
  and 2 MB. Nothing is stored.
- Subtitld: `collab_chat` (merging copies by ID, edits and deletions only by
  the author, unread, `chat.json`); `collab_state` keeps the conversation,
  what waits to be sent, when it was last read, and `save_chat`;
  `collab_service` sends (waiting while offline), asks the first
  collaborator it sees for the conversation, answers anyone who asks; the
  chat card (`interface/subtitldcc_chat.py`) under the co-editing card.
- Tested: the server's rules and signatures, the websocket relay with a
  forged message dropped, the panel (a message pointing at a line and the
  playhead, sending, editing, someone else's edit ignored, the history
  exchange, keeping it in the project, offline), and two Subtitlds against
  a local server (the message from before Bob joined reached him, his reply
  reached Ana).
- Found: Django's `signing.dumps` puts a time in what it makes, so a
  signature over it stopped checking a second later; plain JSON now, and a
  test an hour later.

### Voice

- **Opus over a data channel**, not libdatachannel's media layer (which would
  mean building SRTP in every package): a second, unreliable and unordered
  channel ("voice", no retransmissions) on a link of its own, so a large media
  transfer never delays it. Encrypted by DTLS like the rest; the server sees
  nothing; TURN only when a direct link fails.
- 48 kHz mono, 20 ms frames, Opus at 24–32 kbit/s with its in-band error
  correction; a small jitter buffer (40–80 ms) and Opus's loss concealment for
  missing frames.
- Capture through sounddevice/PortAudio, as `recorder.py` does already;
  playback on an output stream of its own. libopus through ctypes, bundled in
  each package like libdatachannel (Haiku: a spike first).
- **Push-to-talk (default):** a key you hold, configurable (Space plays the
  video, so not that one: a function key, a mouse side button, or the
  on-screen button), and a latch (click to stay on, click again to stop).
  Only while Subtitld has the focus: Wayland allows no global key grabs.
- **Open mic (option, "with headphones"):** voice-activity detection and noise
  suppression, so typing and silence aren't sent. Echo cancellation only if
  people ask for open mic on speakers; it would take Subtitld's own playback
  as its reference, since Subtitld mixes it.
- The video's sound ducks while someone speaks, in both modes.
- Who is in the call and who is speaking show with the collaborators already
  shown (panel, timeline colours).
- Every person links to every other: with many in a call (past about six), a
  subtle note that links may suffer. No limit.

**Voice spike (2026-10-22):**

- `modules/opus.py`: libopus 1.6 through ctypes (48 kHz mono, 20 ms
  frames, 32 kbit/s cap, speech, in-band FEC, DTX). `modules/voice.py`: a
  3-byte header (version, 16-bit sequence) and the jitter buffer: 60 ms in
  hand, a lost frame rebuilt from the next one's FEC, concealment otherwise,
  quiet when the talker stops, at most 300 ms held. `rtc.create_channel(...,
  unreliable=True)`: unordered, no retransmissions.
- Two peers in one process, real pace, 10 s of speech-like audio:

  | Loss | Rebuilt (FEC) | Concealed | Send → playback |
  |---|---|---|---|
  | 0% | 0 | 1 (the end) | 59 ms |
  | 5% | all 25 | 2 | 59 ms |
  | 15% | 65 of 77 | 13 | 79 ms |

  19.6 kbit/s of Opus; encoding and decoding about 1 ms a frame each.
- **Voice needs a link of its own.** With a bulk transfer on the same
  connection, voice took 480 ms and 448 of 500 frames came too late or not
  at all; on a separate connection, beside 92 MB/s of bulk, it stayed at 60
  ms with every frame played.
- libopus in the packages: the KDE Flatpak runtime has it; AUR `opus`; snap
  `libopus0` from the base; Haiku `opus`; AppImage, Windows and macOS build
  it with libdatachannel's scripts. Risk: `opus_encoder_ctl` is variadic,
  which ctypes may pass wrongly on Apple Silicon: check in the macOS CI job
  before voice ships there.
- Not done yet: microphone and speakers (sounddevice), push-to-talk, the
  voice link set-up, a test across two real networks.

**Push-to-talk voice (2026-10-23), built, not yet tried with real
microphones:**

- `modules/collab_voice.py`: a Call with a Link (its own PeerConnection,
  one unreliable channel) to each collaborator in it; the lower connection
  ID offers; set-up as `rtc` messages marked `voice` (collab_service routes
  them, media set-up goes to the transfers). The microphone (the recording
  device chosen in the audio panel) and the speakers are sounddevice
  streams, 48 kHz mono, 20 ms blocks; frames go out only while talking.
- Who is in the call: presence carries `voice`; the call follows it, links
  come and go with it. Who is speaking: frames heard in the last 300 ms.
- The card (under the chat): Join/Leave, Hold to talk (the button), F8
  (configurable, while Subtitld has the focus, through an application-wide
  key filter that is on only during a call; losing the focus stops
  talking), "Stay on the air", the people in the call with who speaks, the
  note past six. The project's sound ducks to 30% while someone speaks
  (audioengine's output callback, eased across the block).
- Tested: two calls in one process over real links (push-to-talk gates the
  frames, latch, who speaks, leaving), and the card (join, presence, key,
  button, latch, speaking and ducking, the note, leaving).
- Left: a try with real microphones and speakers; libopus in the AppImage,
  Windows and macOS builds and in the snap, AUR and Haiku packages; the
  macOS check of `opus_encoder_ctl` through ctypes.

### Why push-to-talk

Subtitle editors play the video all the time (an open mic on speakers picks
up the film and the others' voices: echo) and type all the time (keyboard
noise). Push-to-talk sends only what the person means to say, needs no echo
cancellation, and uses no bandwidth in silence.

### Order

1. Text chat (server relay, catching up from collaborators, saving in the
   project).
2. Voice spike: libopus in the packages, Opus over an unreliable channel, a
   jitter buffer; tested across two networks.
3. Push-to-talk voice: who's in the call and speaking, devices, ducking.
4. Open mic for headphones: voice detection, noise suppression.
5. Echo cancellation, only if needed.

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
