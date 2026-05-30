# pi-airplay

Simple AirPlay 2 receiver on a Raspberry Pi, with a minimal web status page.
No DSP, no LADSPA, no ALSA loopback. Just shairport-sync → headphone jack,
and a small Flask page showing what's playing.

---

## Goal

- AirPlay 2 audio plays through the Pi's 3.5mm jack → amplifier
- A web page (port 8080 or similar) shows whether something is playing,
  and ideally the track name / artist from shairport-sync's metadata pipe
- VU meters would be nice eventually, but are not required to start

---

## Pi connection

- Hostname: `dynamo.local` (mDNS, works on the LAN)
- SSH: `ssh dynamo.local` — keys are already in `~/.ssh/` on this Mac, no password needed
- Username on the Pi: `aal`
- OS: Raspberry Pi OS Bullseye (Debian 11), Pi 3

---

## What is currently running on the Pi — and what to clean up

The previous project (`dynamo-dsp`) installed these services. They should be
**stopped and disabled** before starting fresh:

| Service | What it does | Action |
|---------|-------------|--------|
| `airplay-dsp` | ecasound TAP Dynamics DSP chain | **stop + disable** |
| `dsp-ui` | Flask web UI on port 8080 (old project) | **stop + disable** |
| `shairport-sync` | AirPlay 2 receiver | **keep, but reconfigure** |
| `nqptp` | AirPlay 2 timing daemon | **keep as-is** |

### shairport-sync output fix (important)

During debugging, shairport-sync's output was changed to `hw:Loopback,1`
(to feed the old ecasound chain). It must be changed back so it outputs
directly to the headphone jack:

```
# /etc/shairport-sync.conf
alsa = {
  output_device = "hw:Headphones";
  mixer_control_name = "";
};
```

Then restart shairport-sync.

### snd-aloop (ALSA loopback kernel module)

The old project loaded `snd-aloop` at boot. It is no longer needed.
It can be left loaded (harmless), or unloaded and removed from boot:

```bash
# optional cleanup
sudo rmmod snd-aloop
sudo rm -f /etc/modules-load.d/snd-aloop.conf
sudo rm -f /etc/modprobe.d/snd-aloop.conf
```

### Old project files

The old project lives in `~/camilla/` on the Pi (not `~/dynamo-dsp/` —
deployment was manual). Key files:

- `~/camilla/airplay_dsp.sh` — ecasound launch script (not needed)
- `~/dsp-ui/app.py` — old Flask app (not needed)
- `/etc/systemd/system/airplay-dsp.service`
- `/etc/systemd/system/dsp-ui.service`

---

## shairport-sync metadata

shairport-sync can write now-playing metadata (track, artist, album, play state)
to a named pipe. Enable it in `/etc/shairport-sync.conf`:

```
metadata = {
  enabled = "yes";
  include_cover_art = "no";
  pipe_name = "/tmp/shairport-sync-metadata";
};
```

The pipe emits XML. A simple Python parser can extract track info from it.
Reference: https://github.com/mikebrady/shairport-sync/blob/master/METADATA.md

---

## Why the previous approach was abandoned

The old project (`dynamo-dsp`) routed audio through an ALSA software loopback
(snd-aloop) so that ecasound could apply a dynamic range expander (TAP Dynamics
LADSPA plugin) and simultaneously tap the signal for browser VU meters.

This unravelled because ecasound's multi-output chain routing is broken in
practice: it only delivers audio to the *last* `-o:` argument in its command
line, silently discarding the others. There is no clean fix without either
CamillaDSP (a proper replacement for ecasound) or a more complex ALSA dsnoop
configuration. The DSP feature wasn't essential, so the whole layer was dropped.
