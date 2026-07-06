# pi-airplay

AirPlay 2 receiver on a Raspberry Pi 3, with a minimal web status dashboard.
Audio outputs via S/PDIF optical (TOSLINK) through a HiFiBerry Digi+ Standard HAT.
No DSP, no LADSPA, no ALSA loopback.

See `README.md` for full setup/rebuild instructions and `signal-chain.md` for the
digital signal path analysis.

---

## Pi connection

- Hostname: `dynamo.local` (mDNS on LAN) or `dynamo` (Tailscale MagicDNS, works anywhere)
- SSH: `ssh dynamo.local` (preferred) or `ssh aal@192.168.1.104` (wlan1, static) — keys in `~/.ssh/`, no password. Off-network: `ssh dynamo.taild6cb04.ts.net` (Tailscale).
- Username: `aal`
- OS: Raspberry Pi OS Bullseye (Debian 11), 32-bit, Pi 3

---

## Services

| Service | What it does |
|---------|-------------|
| `shairport-sync` | AirPlay 2 receiver, built from source at `~/shairport-sync/` (development branch), `Restart=always` |
| `nqptp` | AirPlay 2 precision timing daemon |
| `pi-airplay` | Flask status dashboard on port 8080, `Restart=on-failure` |

---

## Key file locations on the Pi

| File | Purpose |
|------|---------|
| `/etc/shairport-sync.conf` | shairport-sync config (output device, name, metadata pipe) |
| `/home/aal/pi-airplay/app.py` | Flask dashboard — source of truth is `app.py` in this repo |
| `/home/aal/pi-airplay/history.json` | Persisted play history (last 25 tracks) |
| `/tmp/shairport-sync-metadata` | Named pipe for now-playing metadata (XML) |
| `/usr/local/bin/restart-airplay` | Helper: restarts nqptp then shairport-sync |
| `/boot/config.txt` | HiFiBerry overlay (`dtoverlay=hifiberry-digi`, `dtparam=audio=off`) |

All system config files are also tracked in `pi-config/` in this repo.

---

## Audio hardware

- HiFiBerry Digi+ Standard HAT (WM8804 S/PDIF transceiver)
- ALSA device: `hw:sndrpihifiberry` (card 1)
- Output: TOSLINK fiber-optic S/PDIF
- No hardware volume mixer — software volume only (shairport-sync handles it)
- Observed ALSA output during playback: S24_LE, 48000 Hz (iOS sends at 48kHz)

---

## Network — important two-WiFi-interface quirks

The Pi has two WiFi interfaces on the same LAN subnet, which requires two config fixes:

**1. Avahi self-conflict** (`/etc/avahi/avahi-daemon.conf`)
- `allow-interfaces=wlan1` pins mDNS to the USB dongle only
- Without this, Avahi hears its own mDNS from the other interface and renames the host to `dynamo-2.local`, breaking AirPlay discovery
- Symptom: AirPlay device disappears; `journalctl -u avahi-daemon` shows `dynamo-2.local`
- Fix: `sudo systemctl restart avahi-daemon`

**2. Asymmetric routing** (`/etc/dhcpcd.conf`)
- `metric 100` on wlan1 makes it the preferred route (wlan0 is metric 303)
- Without this, AirPlay connections arrive on wlan1 but reply packets go out wlan0 — the iPhone drops the session after ~10 seconds
- Symptom: AirPlay connects, plays briefly, drops with iPhone error -15486; `shairport-sync -vvv` shows `feedback unexpected rate: 0.000000`
- wlan0 stays active as automatic fallback if the dongle is removed

---

## Deploy command

```bash
scp app.py dynamo.local:/home/aal/pi-airplay/app.py && ssh dynamo.local "sudo systemctl restart pi-airplay"
```

## shairport-sync rebuild

```bash
ssh dynamo.local "cd ~/shairport-sync && git pull && make -j3 && sudo make install && sudo systemctl restart shairport-sync"
```

No patch needed — the old `audio_alsa.c` crash fix was specific to the previous DSP setup and is no longer required.
