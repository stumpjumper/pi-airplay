# pi-airplay

AirPlay 2 receiver on a Raspberry Pi 3, with a minimal web status dashboard. Audio outputs via S/PDIF optical (TOSLINK) through a HiFiBerry Digi+ Standard HAT.

---

## What it does

- Receives AirPlay 2 audio from an iPhone or Mac and outputs it as S/PDIF optical
- Serves a web dashboard on port 8080 showing what's playing, play history, system health, and a restart button

## Hardware

| Component | Details |
|-----------|---------|
| Pi | Raspberry Pi 3 |
| HAT | HiFiBerry Digi+ Standard (WM8804 S/PDIF transceiver) |
| Output | TOSLINK fiber-optic S/PDIF |
| WiFi | Internal (wlan0) + USB dongle (wlan1, primary) |
| Hostname | `dynamo` / `dynamo.local` / Tailscale MagicDNS |

## Services

| Service | What it does |
|---------|-------------|
| `shairport-sync` | AirPlay 2 receiver — built from source, development branch |
| `nqptp` | AirPlay 2 precision timing daemon |
| `pi-airplay` | Flask status dashboard on port 8080 |

---

## Repository layout

```
app.py                        Flask status dashboard
pi-airplay.service            systemd unit for the dashboard
pi-config/                    Pi system config files (deploy to matching paths)
  boot/config.txt             → /boot/config.txt
  etc/shairport-sync.conf     → /etc/shairport-sync.conf
  etc/avahi/avahi-daemon.conf → /etc/avahi/avahi-daemon.conf
  etc/dhcpcd.conf             → /etc/dhcpcd.conf
  etc/sudoers.d/pi-airplay-restart → /etc/sudoers.d/pi-airplay-restart
  usr-local-bin/restart-airplay    → /usr/local/bin/restart-airplay
signal-chain.md               Full AirPlay 2 → S/PDIF digital signal path
```

---

## Setup from scratch (new Pi)

### 1. OS

Raspberry Pi OS Bullseye (Debian 11), 32-bit. Use the Raspberry Pi Imager; set hostname to `dynamo`, enable SSH, configure WiFi.

### 2. HiFiBerry Digi+ Standard

Deploy `pi-config/boot/config.txt` to `/boot/config.txt`. The key lines:

```
dtparam=audio=off
dtoverlay=hifiberry-digi
```

Reboot after writing this file.

### 3. nqptp

Required for AirPlay 2. Build from source:

```bash
git clone https://github.com/mikebrady/nqptp.git
cd nqptp
autoreconf -fi
./configure --with-systemd-startup
make
sudo make install
sudo systemctl enable nqptp
sudo systemctl start nqptp
```

### 4. shairport-sync

Build from source (development branch). Install dependencies first:

```bash
sudo apt install --no-install-recommends build-essential git autoconf automake libtool \
  libpopt-dev libconfig-dev libasound2-dev avahi-daemon libavahi-client-dev libssl-dev \
  libsoxr-dev libplist-dev libsodium-dev libavutil-dev libavcodec-dev libavformat-dev \
  uuid-dev libgcrypt-dev xxd
```

Build:

```bash
git clone https://github.com/mikebrady/shairport-sync.git
cd shairport-sync
git checkout development
autoreconf -fi
./configure --sysconfdir=/etc --with-alsa --with-soxr --with-avahi --with-ssl=openssl \
  --with-airplay-2 --with-metadata
make -j3
sudo make install
sudo systemctl enable shairport-sync
sudo systemctl start shairport-sync
```

Deploy `pi-config/etc/shairport-sync.conf` to `/etc/shairport-sync.conf`, then restart shairport-sync.

### 5. Network config (important — two WiFi interfaces)

The Pi has two WiFi interfaces on the same subnet, which causes mDNS self-conflicts and AirPlay routing failures without these fixes.

Deploy both files and restart the affected services:

```bash
# Avahi: advertise only on wlan1 to prevent Avahi self-conflict
sudo cp pi-config/etc/avahi/avahi-daemon.conf /etc/avahi/avahi-daemon.conf
sudo systemctl restart avahi-daemon

# dhcpcd: make wlan1 (dongle) the preferred route to fix AirPlay feedback routing
sudo cp pi-config/etc/dhcpcd.conf /etc/dhcpcd.conf
sudo systemctl restart dhcpcd
```

See `signal-chain.md` and the Claude memory files for the full explanation of why these are needed.

### 6. Flask dashboard

Install Flask:

```bash
pip3 install flask
```

Deploy the app and service:

```bash
mkdir -p ~/pi-airplay
cp app.py ~/pi-airplay/
sudo cp pi-airplay.service /etc/systemd/system/
sudo systemctl enable pi-airplay
sudo systemctl start pi-airplay
```

### 7. restart-airplay helper script

```bash
sudo cp pi-config/usr-local-bin/restart-airplay /usr/local/bin/restart-airplay
sudo chmod +x /usr/local/bin/restart-airplay
sudo cp pi-config/etc/sudoers.d/pi-airplay-restart /etc/sudoers.d/pi-airplay-restart
sudo chmod 440 /etc/sudoers.d/pi-airplay-restart
```

---

## Dashboard

Visit `http://dynamo.local:8080` on the LAN. Shows:

- Now playing: track, artist, album, source device, volume
- Health banner: green/amber/red based on service state
- System info: uptime, CPU temp, memory, load
- Play history: last 25 tracks
- Restart AirPlay button

---

## Network notes

- `dynamo.local` — mDNS via Avahi (LAN only)
- `dynamo` — Tailscale MagicDNS (works anywhere on the tailnet)
- wlan0 (internal, 192.168.1.103) stays active as fallback if dongle is removed
- wlan1 (USB dongle, 192.168.1.104) is the primary interface (better signal, lower metric)

## Signal chain

See `signal-chain.md` for the full AirPlay 2 → S/PDIF digital signal path, including observed format (48kHz/24-bit S24_LE at the ALSA layer) and signal quality analysis.
