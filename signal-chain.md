# AirPlay 2 → HiFiBerry Digi+ Signal Chain

This document describes the complete digital signal processing chain from an iPhone sending audio via AirPlay 2 to the S/PDIF (TOSLINK) output of a HiFiBerry Digi+ Standard HAT on a Raspberry Pi 3.

---

## Hardware

- **Source:** iPhone running Apple Music
- **Receiver:** Raspberry Pi 3, Raspberry Pi OS Bullseye (Debian 11), 32-bit
- **HAT:** HiFiBerry Digi+ Standard (WM8804 S/PDIF transceiver)
- **Output:** TOSLINK fiber-optic S/PDIF

---

## Stage 1 — AirPlay 2 Transmission (iPhone → Pi over WiFi)

AirPlay 2 uses a **buffered audio** transport model (TCP-based, unlike AirPlay 1's UDP). The iPhone negotiates the session via RTSP on port 7000, then streams compressed audio over a separate TCP connection.

- **Codec on wire:** AAC-LC (`ct=4` in AirPlay 2 SETUP plist), audio format index 23 (`audioFormat = 0x800000`), 1024 samples per packet
- **Sample rate transmitted:** 48000 Hz — iOS runs its audio subsystem at 48000 Hz and resamples all content to that rate before sending via AirPlay 2, regardless of the source content's native rate (typically 44100 Hz for Apple Music). This SRC happens inside iOS, before transmission.
- **Bit depth at source:** 16-bit (standard Apple Music streams); lossless Apple Music would be ALAC at up to 24-bit/192kHz but typical streaming is 16-bit AAC
- **Precision timing:** NQPTP daemon (Network Quality PTP) runs alongside shairport-sync and handles AirPlay 2's clock synchronization, allowing gapless and multi-room sync

---

## Stage 2 — shairport-sync (Decoding & Volume)

**shairport-sync** version `5.1-dev-33` (development branch) handles receipt and decoding.

### Decoding
- Decoder in use: **ffmpeg** (`decoder_in_use = 4`). AAC-LC is decoded to raw PCM in memory.
- Output of decode: interleaved stereo PCM, 48000 Hz, 16-bit

### Interpolation / Clock Recovery
- shairport-sync uses **soxr** (libsoxr) for sample rate interpolation to compensate for clock drift between the iPhone and the Pi. Soxr is a high-quality resampler.
- Interpolation mode: `auto` (shairport-sync selects based on conditions)
- At 48000 Hz input, no sample rate conversion is needed to reach the ALSA output rate — soxr's role here is purely clock drift correction, not SRC.

### Volume Control
- Volume is applied **in software** by shairport-sync before sending to ALSA. There is no hardware mixer on the HiFiBerry Digi+ (it is a pure digital output device with no DAC and no analog gain stage).
- Volume range: 60 dB (`volume_range_db = 60` in config)
- Default AirPlay volume: −24 dBFS
- **Signal quality implication:** Software volume attenuation is implemented as PCM scaling (multiplication). At any volume below 0 dBFS, this reduces the effective bit depth. At −6 dB, roughly 1 bit of resolution is lost; at −60 dB, 10 bits are lost. At 100% volume (0 dB attenuation), the PCM is passed through unmodified. Dithering is enabled (`enabling dither` in logs) to mitigate truncation artifacts when scaling reduces the bit depth.

### No DSP
There is no equalizer, no dynamic range processing, no LADSPA plugins, and no room correction anywhere in this chain. The signal goes from decoded PCM directly to ALSA.

---

## Stage 3 — ALSA (Linux Audio Layer)

shairport-sync writes to ALSA using the **direct hardware interface**: `hw:sndrpihifiberry` (card 1, device 0).

- The `hw:` prefix bypasses ALSA's software mixing layer (dmix) entirely — no resampling, no mixing, no format conversion is applied by ALSA itself.
- Format negotiation: shairport-sync probes the HiFiBerry device for supported formats and rates, then selects the best match. Supported formats: S16_LE, S24_LE. Supported rates: 32000, 44100, 48000, 64000, 88200, 96000, 176400, 192000 Hz.
- **Observed output** (verified via `/proc/asound/card1/pcm0p/sub0/hw_params` during live playback): S24_LE, 48000 Hz, stereo. shairport-sync selected 24-bit output to give itself headroom for volume scaling and dithering, even though the source is 16-bit. ALSA passes this directly to the driver with no further conversion.

---

## Stage 4 — HiFiBerry Digi+ Standard HAT (I²S → S/PDIF)

The HiFiBerry Digi+ Standard uses a **Wolfson WM8804** S/PDIF transceiver.

- The Pi communicates with the HAT over **I²S** (Inter-IC Sound), a synchronous serial bus. The driver is loaded via `dtoverlay=hifiberry-digi` in `/boot/config.txt`. The built-in Pi audio is disabled (`dtparam=audio=off`).
- The WM8804 receives the I²S PCM stream and re-encodes it as **S/PDIF** (IEC 60958), wrapping the PCM samples in S/PDIF framing with embedded channel status bits and validity flags.
- **Output:** TOSLINK fiber-optic S/PDIF at the sample rate of the incoming stream (48000 Hz as observed).
- There is **no DAC** on the Digi+ Standard — the output is purely digital. No analog stages, no op-amps, no capacitors in the signal path.

### Clock / Jitter
- The WM8804 has an internal **PLL** that locks to the incoming I²S clock from the Pi. The quality of the S/PDIF output jitter depends on this PLL and the stability of the Pi's I²S master clock.
- The Pi's I²S clock is derived from its internal oscillator, which is not audiophile-grade but is adequate for standard consumer S/PDIF. Any connected DAC with its own asynchronous reclocker (e.g. a receiver chip like CS8416 or DIR9001 with internal PLL) will largely reject upstream jitter.
- The HiFiBerry Digi+ **Pro** (not installed here) includes a dedicated low-jitter oscillator; the Standard relies on the Pi's clock.

---

## Summary Table

| Stage | Process | Format In | Format Out | Quality Notes |
|---|---|---|---|---|
| iPhone | iOS audio SRC + AAC-LC encode | PCM 44.1k/16 | AAC-LC 48k/16 | iOS resamples to 48k before sending; lossy compression |
| WiFi | TCP buffered stream | AAC-LC 48k | AAC-LC 48k | No degradation |
| shairport-sync | AAC-LC decode | AAC-LC 48k/16 | PCM 48k/16 | ffmpeg decoder |
| shairport-sync | soxr clock correction | PCM 48k/16 | PCM 48k/16 | Drift correction only, no SRC |
| shairport-sync | Software volume + dither | PCM 48k/16 | PCM 48k/24 | Upscaled to 24-bit for headroom; bit depth loss if < 0 dB, dithered |
| ALSA `hw:` | Direct passthrough | PCM 48k/24 | PCM 48k/24 | No conversion |
| HiFiBerry / WM8804 | I²S → S/PDIF encode | PCM 48k/24 | S/PDIF 48k/24 | Pi clock quality; PLL jitter |
| TOSLINK | Fiber optic | S/PDIF | S/PDIF | No degradation |

---

## Key Signal Quality Factors

1. **AAC-LC lossy compression** is the largest quality limiting factor. Apple Music standard streams are AAC at 256 kbps, which is transparent to most listeners but is not lossless.
2. **Software volume below 0 dBFS** reduces effective bit depth. At the default −24 dB AirPlay volume, approximately 4 bits of resolution are sacrificed (partially recovered by dithering).
3. **Jitter** from the Pi's I²S clock is the main hardware limitation. A downstream DAC with good jitter rejection will largely negate this.
4. **No DSP processing** — the chain is otherwise a straight wire from decode to S/PDIF.
