# Glimpse launch film: storyboard

**Length:** 84.0 s (35 bars), 1920×1080, 30 fps.
**Music:** 100 BPM in 4/4, so a beat is 0.6 s and a bar is 2.4 s. The key is D major.
**Promise:** *Every moment you've saved is on your PC. Glimpse finds it by what's in it, privately, with Google's EmbeddingGemma 2 running on your own GPU.*

The film is a pure function of time. `story/timeline.js` is the single clock; every number below comes from it. Picture, physics (`sim.js`) and music (`score.py`) all read the same timeline, so they cannot drift.

**Media.** Every image is the creator's real media, prepared by `prep.py` into `video/private/media/` (gitignored):
- 900 personal photos for the pile.
- The real results of real searches run against the live app: cards, full-screen heroes, PDF page, code, settings and their live controls.
- Frames of one real matched video moment (a birthday, someone blowing out candles).
- The real index counts: 29,147 photos, 10,781 video moments, 237,813 documents, 5,719 recordings.

## Look

- **Background.** Deep night blue-black (#07080c to #0d1020) with soft blue (#3466f6) and violet (#8b5cf6) light blooms. A fine film grain covers everything; the vignette is subtle.
- **Photos.**
  - Real prints, 6 px rounded corners, with a thin white inner border (like a printed photo).
  - Soft drop shadow.
  - When lit, a slight specular sheen.
- **Type.**
  - "Segoe UI Variable Display" 700 for headlines (96 to 120 px, tracking -0.035em). Captions are 600 weight, 54 px.
  - Headlines are white; one key word uses the brand gradient (#8fb2ff to #b79bff).
- **App UI.** The real Glimpse launcher look:
  - A glass panel, rgba(19,22,30,.86) with a 20 px backdrop blur and 26 px radius.
  - A search bar with a soft blue focus ring.
  - Section headers such as "Photos & screenshots".
- **Motion.**
  - Everything moves: cameras drift or push.
  - Hits land on beats, with anticipation, overshoot and squash on impacts.
  - Text never pops: it rises 24 px and unblurs over 0.35 s, and leaves the same way, reversed.
- **No randomness.** Use `hash(i)` and `noise(t)` from core.js. Never use `Math.random()` or the clock.

## Act 1: MEMORIES (0 to 14.4 s, bars 0 to 5)

Music: solo felt piano arpeggios (D, A/C#, Bm, G, D, A), a pad from bar 3, and a riser from 12 s. Every collision in the pile is a soft pitched mallet note in D major pentatonic: the pile **plays music** as it falls.

| t (s) | Picture | Sound |
|---|---|---|
| 0.0 | Black. Warm dust motes drift. | Room tone, felt-piano D |
| 0.6 | Line 1 rises: **"Every moment you've ever saved…"** | |
| 2.4 | First big photo (440 px) falls into frame and lands exactly on the beat with a little bounce. Line 1 lifts away. | Each landing is a piano note |
| 2.4 to 7.2 | Eight big photos land on quarter notes. The camera is close (zoom 1). | |
| 7.2 | Line 2: **"…is somewhere on your PC."** Eight medium photos land on 8th notes; the camera pulls back (zoom to 0.62). | Pad enters |
| 9.6 | Sixteen small photos on 16ths, and the camera keeps pulling back (zoom to 0.36). The counter rises at the bottom: **29,147 photos · 10,781 videos · 237,813 documents**. | Piano doubles |
| 10.8 to 14.1 | THE FLOOD: hundreds of photos pour in, accelerating from 8 to 160 per beat. A far background "rain" of thousands of tiny photos (not physics) adds depth. | Mallet notes turn into a shimmering wash, riser |
| 12.0 | Line 3, big: **"So how do you find the one you want?"** | |
| 12.9 to 14.4 | SLOW MOTION (physics at 0.22×): photos hang in the air, and the search results are visible, drifting. A blue light leaks from the bottom. | Riser peaks, then sudden silence at 14.1 |

## Act 2: THE TURN (14.4 to 16.8 s, bar 6)

| t | Picture | Sound |
|---|---|---|
| 14.4 | **HIT.** Giant keycaps **Alt + Space** slam down (squash), and a shockwave ring spreads. The whole pile **jumps** (physics impulse) and the camera shakes. | Boom, crash, drums and bass enter |
| 14.4 to 16.8 | The pile falls back with real collisions. The camera reframes so the pile fills the lower 40% of the frame, blurred and dimmed. The keycaps shrink and morph into the search bar, and the glass launcher opens. | Clatter of collisions (quieter), groove starts |

## Act 3: ASK (16.8 to 64.8 s, bars 7 to 26): ten search slots, 2 bars (4.8 s) each

Each search follows the same beat map (see `TL.QUERIES`):
- **Beats 0 to 2.5: type.** Keystrokes fall on 16ths, or faster for long queries, so each keystroke is a hi-hat tick. The voice search is spoken instead: the mic turns red, a live waveform appears, then the words drop in on 16ths.
- **Beat 2.5 (Enter):** the real latency shows. Photo results **tear out of the pile** and fly into the grid; text results slide in as rows or cards.
- **Beat 4 (hero):** the best result opens: a full-screen photo or video moment, the PDF page with the sentence highlighted, the code around the hit, or the settings card. A caption sits lower-left.

| # | Start | Query | Caption (line 1 / line 2) |
|---|---|---|---|
| 1 | 16.8 | `paneer` | **Type a word.** / Every photo of it, instantly. |
| 2 | 21.6 | `birthday` | **Even moments inside videos.** / It opens the clip right where it happens. |
| 3 | 26.4 | `kayaking under a bridge` | **Describe it like you would to a friend.** / It understands what's in the picture. Faces in these photos are blurred (`blur_faces.py`). |
| 4 | 31.2 | `mountains and a lake` | **No tags. No albums. No filenames.** / It just knows what you mean. |
| 5 | 36.0 | `temple`, in a folder, then the whole PC | **Search this folder. Or the whole PC.** / Open it from File Explorer. Tab switches. The launcher closes and a File Explorer window rises (the phone-backup folder); **Alt + Space** reopens Glimpse over it with a folder chip in the search bar, and the results come only from that folder. Then **Tab**: the chip flips to This PC and the same search widens to everything. |
| 6 | 40.8 | `what is a bayesian network` | **Documents, by meaning.** / The exact page. The exact sentence. |
| 7 | 45.6 | `code that finds duplicate photos` | **Code, by what it does.** / Even when you forgot what you named it. |
| 8 | 50.4 | `my screen is too dim` | **Not just your files. Your whole PC.** / Say what's wrong. Fix it right there. The whole frame dims while it is typed; the Display settings card opens with the app's live brightness slider, a cursor drags it from 30% to 90% and the film brightens with it. |
| 9 | 55.2 | rapid-fire: `make text bigger` · `eyes hurt at night` · `no sound from speakers` · `uninstall an app` | **Settings. Apps. Fixes.** / One search box for all of Windows. Four quick searches, 2 beats each: typed on the "and", Enter on the beat, the top answer as one big card (the sound one with its volume slider). |
| 10 | 60.0 | 🎙 "sunset at the beach" | **Or just say it.** / Voice search runs on your PC too. |

## Act 4: THE BRAIN (64.8 to 72.0 s, bars 27 to 29): EmbeddingGemma 2, local, private

Music is a breakdown: drums drop out, with piano, a warm pad and a heartbeat sub. A riser runs from 69.6 s.

| t | Picture |
|---|---|
| 64.8 | The pile **lifts off** and the photos stream up into a slowly rotating 3D cloud, a "meaning space". Similar photos cluster together (food with food, mountains with mountains, people with people). Among them float other kinds of things: PDF pages, waveforms of voice notes, video frames, and settings icons. |
| 66.0 | Title: **"Powered by EmbeddingGemma 2"**, with the sub-line "Google's open model that understands text, photos, video and speech in one shared space". |
| 67.2 | A query line, "mountains and a lake", shoots into the cloud as a glowing vector. Its nearest neighbours light up and pull toward it, which shows how search by meaning works. |
| 68.4 | A laptop outline draws itself around the cloud, and a GPU chip glows. **"It runs on your PC."** A cloud icon outside has its link cut: **"Nothing is uploaded. Ever."** A small line: "Works offline · DirectX 12 GPU or CPU". |
| 69.6 | Implosion: everything is pulled to the centre and spins faster, and the light gathers. |

## Act 5: FINALE (72.0 to 84.0 s, bars 30 to 34)

| t | Picture | Sound |
|---|---|---|
| 72.0 | **HIT.** The gathered light explodes; hundreds of photos fly outward toward the camera in 3D (scaling up, motion-blurred, spinning) and clear to reveal the app icon. | The biggest boom and chord, then the final chorus |
| 73.2 | **Glimpse** wordmark | |
| 74.4 | Tagline: **"Find the moment, not the file."** | |
| 76.8 | "Free & open source · any Windows 10 or 11 PC · one-line install", with keycaps **Alt + Space**. | |
| 79.2 | `github.com/Ayushmaniar/glimpse`, and a soft glow on the install one-liner. A row of chips lands on 16ths: Search this folder · Search by image · "photos from last summer" · Scanned pages · Voice search · API keys masked. | |
| 81.6 to 84.0 | Slow drift; a few photos float like embers; fade to black. | Sustained D major chord, piano tail |
