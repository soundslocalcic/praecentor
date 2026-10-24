Song authoring
==============

A Praecentor song is authored as a YAML file that describes the track layout,
render output, musical timing, and the event sequence for the song.

Core structure
--------------

A typical song definition looks like this:

.. code-block:: yaml

   version: 1

   meta:
     title: "Feed My Frankenstein"
     artist: "Alice Cooper"
     key: "D minor"

   audio:
     stems:
       - group: "backing"
         label: "Music"
         volume: .9
         tracks:
           - name: "base"
             file: "Bounces/Backing.mp3"
             label: "Backing"

   video:
     layout:
       file: "/path/to/layout.yaml"
     output:
       - file: "Video/Performance.mp4"
         tracks:
           - backing.base

   timing:
     lyrics:
       source: tap
       map_file: "Bounces/Lyrics.yaml"
       base_offset_seconds: -.6
       max_line_chars: 36
       lead_in_beats: 2
     beats:
       source: auto
       click_track:
         file: "Bounces/Click.mp3"

   riffs:
     chorus1: "D1 F1 G0.5 F0.5 D1 D0.5 D0.5"

   chords:
     verse: "Dm"
     prechorus: "Am"

   events:
     - marker: "Verse 1"
       lyric: |
         Well, I ain't evil, I'm just
         good lookin'
       chords: "@verse"

Key sections
------------

``meta``
   Song title, artist, and key information.

``audio``
   Track groups and stem files. This is where backing and vocal sources are
   declared.

``video``
   Configures render outputs and optional layout overrides.

``timing``
   Controls lyric timing, beat timing, offsets, and timing-map references.

``riffs``
   Reusable riff patterns rendered as musical prompts.

``chords``
   Named chord labels that can be reused across events.

``events``
   The song timeline: sections, lyric blocks, instructions, riffs, and chord
   changes.

Event fields
------------

Each event is a mapping with one of the following main payloads:

- ``lyric`` for lyric text that should scroll onscreen
- ``instruction`` for section labels or commands with no lyric line
- ``riff`` for a visible riff prompt
- ``chords`` for the active chord or chord reference
- ``marker`` for section headings
- ``root`` to override the root note interpretation for a riff

A single event may contain lyric text while also setting a chord or riff.

Best practice
-------------

Keep the YAML file readable and structured by section. It is usually easier to
maintain if:

- each song has a single authoritative timeline file
- riffs and chords are reused via named anchors where possible
- timing maps are kept alongside the audio source rather than embedded inline
- the event list stays grouped by section markers such as Verse, Chorus, and Break
