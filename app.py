#!/usr/bin/env python3
import base64
import json
import plistlib
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime
from flask import Flask, jsonify

PIPE           = "/tmp/shairport-sync-metadata"
HISTORY_FILE   = "/home/aal/pi-airplay/history.json"

def _h(s):
    return s.encode().hex()

SSNC  = _h("ssnc")
MINM  = _h("minm")
ASAR  = _h("asar")
ASAL  = _h("asal")
PBEG  = _h("pbeg")
PEND  = _h("pend")
PAUS  = _h("paus")
PRSM  = _h("prsm")
ABEG  = _h("abeg")
AEND  = _h("aend")
MDST  = _h("mdst")
MDEN  = _h("mden")
SNAM  = _h("snam")
PVOL  = _h("pvol")
COPL  = _h("copl")



def get_mem_pct():
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":")
                info[k.strip()] = int(v.strip().split()[0])
        used = info["MemTotal"] - info["MemAvailable"]
        return round(used / info["MemTotal"] * 100)
    except Exception:
        return None


def get_load():
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
        return f"{parts[0]}  {parts[1]}  {parts[2]}"
    except Exception:
        return None


def get_uptime():
    try:
        with open("/proc/uptime") as f:
            secs = float(f.read().split()[0])
        d = int(secs // 86400)
        h = int((secs % 86400) // 3600)
        m = int((secs % 3600) // 60)
        parts = []
        if d: parts.append(f"{d}d")
        if h: parts.append(f"{h}h")
        parts.append(f"{m}m")
        return " ".join(parts)
    except Exception:
        return None


def get_cputemp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000, 1)
    except Exception:
        return None


def get_service_info():
    try:
        r = subprocess.run(
            ["systemctl", "show", "shairport-sync",
             "--property=ActiveState,NRestarts,ActiveEnterTimestampMonotonic"],
            capture_output=True, text=True, timeout=3
        )
        props = {}
        for line in r.stdout.splitlines():
            k, _, v = line.partition("=")
            props[k.strip()] = v.strip()

        active   = props.get("ActiveState") == "active"
        restarts = int(props.get("NRestarts", "0") or "0")

        mono_us = int(props.get("ActiveEnterTimestampMonotonic", "0") or "0")
        service_age_secs = None
        if mono_us > 0:
            with open("/proc/uptime") as f:
                uptime_secs = float(f.read().split()[0])
            service_age_secs = uptime_secs - mono_us / 1e6

        return {"active": active, "restarts": restarts, "service_age_secs": service_age_secs}
    except Exception:
        return {"active": False, "restarts": 0, "service_age_secs": None}


_log_cache: dict = {"lines": [], "ts": 0.0}

def get_recent_logs():
    now = time.time()
    if now - _log_cache["ts"] < 10:
        return _log_cache["lines"]
    try:
        r = subprocess.run(
            ["journalctl", "-u", "shairport-sync", "-n", "10", "--no-pager", "--output=short"],
            capture_output=True, text=True, timeout=5
        )
        lines = [l for l in r.stdout.splitlines() if l and not l.startswith("--")][-10:]
        _log_cache.update({"lines": lines, "ts": now})
        return lines
    except Exception:
        return _log_cache["lines"]


state   = {"title": None, "artist": None, "album": None,
           "playing": False, "source": None, "volume": None}
staging: dict = {}

# Diagnostic trace of recent metadata pipe items, served at /debug
trace: deque = deque(maxlen=100)

def _hex2ascii(h):
    try:
        return bytes.fromhex(h).decode("ascii")
    except Exception:
        return h


def _load_history():
    try:
        with open(HISTORY_FILE) as f:
            return deque(json.load(f), maxlen=25)
    except Exception:
        return deque(maxlen=25)

history = _load_history()
lock    = threading.Lock()

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pi AirPlay</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, sans-serif;
           max-width: 520px; margin: 32px auto; padding: 0 20px;
           color: #222; background: #fff; }

    /* ── Health banner ── */
    .health {
      padding: 14px 16px; border-radius: 10px; margin-bottom: 24px;
      display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
    }
    .health.ok   { background: #e8f5e8; }
    .health.warn { background: #fff8e0; }
    .health.down { background: #fde8e8; }
    .health-info { flex: 1; min-width: 0; }
    .health-title { font-size: 1rem; font-weight: 700; margin-bottom: 3px; }
    .health.ok   .health-title { color: #1a6e1a; }
    .health.warn .health-title { color: #7a5000; }
    .health.down .health-title { color: #8b0000; }
    .health-action { font-size: .875rem; }
    .health.ok   .health-action { color: #2e7d2e; }
    .health.warn .health-action { color: #7a5000; font-weight: 600; }
    .health.down .health-action { color: #8b0000; font-weight: 700; }
    .restart-btn {
      padding: 8px 16px; border: none; border-radius: 8px;
      cursor: pointer; font-weight: 600; white-space: nowrap; flex-shrink: 0;
      font-size: .875rem; transition: opacity .15s;
    }
    .restart-btn:disabled { opacity: .45; cursor: wait; }
    .health.ok   .restart-btn { background: #c5e6c5; color: #1a5c1a; }
    .health.warn .restart-btn { background: #f0c030; color: #5a3a00;
                                font-size: 1rem; padding: 10px 20px; }
    .health.down .restart-btn { background: #c0392b; color: #fff;
                                font-size: 1.05rem; padding: 12px 24px; width: 100%; }

    /* ── Now playing ── */
    #status-line { font-size: .78rem; font-weight: 700; color: #aaa;
                   letter-spacing: .08em; text-transform: uppercase; margin-bottom: 10px; }
    #track  { font-size: 1.5rem; font-weight: bold; margin-bottom: 4px; min-height: 1.8rem; }
    #artist { font-size: 1rem; color: #444; margin-bottom: 2px; min-height: 1.2rem; }
    #album  { font-size: .9rem; color: #888; font-style: italic;
              margin-bottom: 16px; min-height: 1.1rem; }
    .meta-row { display: flex; align-items: center; gap: 16px;
                font-size: .85rem; color: #666; margin-bottom: 4px; }
    #source   { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .vol-wrap { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
    .vol-bar  { width: 80px; height: 4px; background: #ddd; border-radius: 2px; }
    .vol-fill { height: 100%; background: #888; border-radius: 2px; transition: width .4s; }
    #vol-pct  { width: 2.5em; text-align: right; }

    hr { border: none; border-top: 1px solid #eee; margin: 18px 0; }

    /* ── System info ── */
    #sysinfo { font-size: .8rem; color: #aaa;
               display: flex; flex-wrap: wrap; gap: 14px; margin-bottom: 14px; }
    .svc-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%;
               margin-right: 4px; vertical-align: middle; background: #ccc; }
    .svc-dot.up   { background: #5cb85c; }
    .svc-dot.down { background: #d9534f; }

    /* ── Service log ── */
    .section-label { font-size: .72rem; font-weight: 700; color: #bbb;
                     letter-spacing: .08em; text-transform: uppercase; margin-bottom: 8px; }
    .log-box { background: #f7f7f7; border-radius: 8px; padding: 10px 12px;
               font-family: ui-monospace, monospace; font-size: .72rem;
               overflow-x: auto; line-height: 1.6; }
    .log-line { display: block; white-space: pre-wrap; word-break: break-all; color: #777; }
    .log-line.is-fatal, .log-line.is-error { color: #c0392b; font-weight: 700; }
    .log-line.is-warn  { color: #c47000; }
    .log-line.is-start { color: #2e7d2e; }

    /* ── History ── */
    .h-row { display: flex; gap: 10px; align-items: baseline;
             padding: 6px 0; border-bottom: 1px solid #f0f0f0; font-size: .85rem; }
    .h-row:last-child { border-bottom: none; }
    .h-time   { color: #bbb; font-size: .75rem; white-space: nowrap; flex-shrink: 0; width: 8em; }
    .h-title  { font-weight: 500; }
    .h-artist { color: #888; }
    .h-album  { color: #bbb; font-size: .78rem; font-style: italic; }
  </style>
</head>
<body>

  <!-- Health banner -->
  <div id="health-banner" class="health ok">
    <div class="health-info">
      <div id="health-title" class="health-title">Checking...</div>
      <div id="health-action" class="health-action"></div>
    </div>
    <button id="restart-btn" class="restart-btn" onclick="restartAirplay()">↺ Restart</button>
  </div>

  <!-- Now playing -->
  <div id="status-line">Loading...</div>
  <p id="track"></p>
  <p id="artist"></p>
  <p id="album"></p>
  <div class="meta-row">
    <span id="source"></span>
    <div class="vol-wrap" id="vol-wrap" style="display:none">
      <div class="vol-bar"><div class="vol-fill" id="vol-fill"></div></div>
      <span id="vol-pct"></span>
    </div>
  </div>

  <hr>

  <!-- System info -->
  <div id="sysinfo">
    <span id="uptime"></span>
    <span id="cputemp"></span>
    <span id="mem"></span>
    <span id="load"></span>
    <span><span class="svc-dot" id="svc-dot"></span><span id="svc-label">AirPlay</span></span>
  </div>

  <hr>

  <!-- Service log -->
  <div class="section-label">Service log</div>
  <div class="log-box" id="log-box"><span class="log-line">Loading...</span></div>

  <!-- Play history -->
  <div id="history-section" style="display:none; margin-top:20px;">
    <div class="section-label" style="margin-bottom:10px;">Recent plays</div>
    <div id="history-list"></div>
  </div>

  <script>
    function esc(s) {
      return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    function fmtAge(secs) {
      if (secs === null || secs === undefined || secs < 0) return '';
      if (secs < 60)   return Math.round(secs) + 's';
      if (secs < 3600) return Math.round(secs / 60) + 'm';
      var h = Math.floor(secs / 3600);
      var m = Math.round((secs % 3600) / 60);
      return h + 'h ' + m + 'm';
    }

    function updateHealth(d) {
      var banner   = document.getElementById('health-banner');
      var title    = document.getElementById('health-title');
      var action   = document.getElementById('health-action');
      var btn      = document.getElementById('restart-btn');
      var active   = d.svc_active;
      var restarts = d.svc_restarts || 0;
      var age      = d.svc_age_secs;

      if (!active) {
        banner.className   = 'health down';
        title.textContent  = '● AirPlay service is DOWN';
        action.textContent = 'Tap Restart below, then reconnect AirPlay from your device';
        btn.textContent    = '↺ Restart AirPlay';
      } else if (restarts > 0 && age !== null && age < 300) {
        banner.className   = 'health warn';
        title.textContent  = '● AirPlay crashed and restarted ' + fmtAge(age) + ' ago';
        action.textContent = '→ Reconnect AirPlay from your device now';
        btn.textContent    = '↺ Restart AirPlay';
      } else {
        banner.className = 'health ok';
        var parts = ['● AirPlay ready'];
        if (age !== null) parts.push(fmtAge(age));
        if (restarts > 0) parts.push(restarts + ' auto-restart' + (restarts !== 1 ? 's' : ''));
        title.textContent  = parts.join(' · ');
        action.textContent = '';
        if (!btn.disabled) btn.textContent = '↺ Restart';
      }
    }

    function updateLogs(lines) {
      var box = document.getElementById('log-box');
      if (!lines || !lines.length) {
        box.innerHTML = '<span class="log-line">No log entries.</span>';
        return;
      }
      box.innerHTML = lines.map(function(raw) {
        // Strip date + hostname, keep "HH:MM:SS process: message"
        var m = raw.match(/\w{3}\s+\d+\s+(\d{2}:\d{2}:\d{2})\s+\S+\s+(.*)/);
        var text = m ? m[1] + ' ' + m[2] : raw;
        var lo = raw.toLowerCase();
        var cls = 'log-line';
        if (lo.includes('fatal'))                                   cls += ' is-fatal';
        else if (lo.includes('error'))                              cls += ' is-error';
        else if (lo.includes('warning'))                            cls += ' is-warn';
        else if (lo.includes('started') || lo.includes('starting')) cls += ' is-start';
        return '<span class="' + cls + '">' + esc(text) + '</span>';
      }).join('');
    }

    function restartAirplay() {
      var btn = document.getElementById('restart-btn');
      btn.disabled = true;
      btn.textContent = 'Restarting…';
      fetch('/restart_airplay', {method: 'POST'})
        .then(function() { setTimeout(function() { btn.disabled = false; }, 10000); })
        .catch(function() { btn.disabled = false; });
    }

    function poll() {
      fetch('/status').then(function(r) { return r.json(); }).then(function(d) {
        document.getElementById('status-line').textContent = d.playing ? 'Now playing' : 'Not playing';
        document.getElementById('track').textContent  = d.title  || '';
        document.getElementById('artist').textContent = d.artist || '';
        document.getElementById('album').textContent  = d.album  || '';
        document.getElementById('source').textContent = d.source ? '▶ ' + d.source : '';

        var vw = document.getElementById('vol-wrap');
        if (d.volume !== null && d.volume !== undefined) {
          vw.style.display = 'flex';
          document.getElementById('vol-fill').style.width = d.volume + '%';
          document.getElementById('vol-pct').textContent  = d.volume + '%';
        } else {
          vw.style.display = 'none';
        }

        document.getElementById('uptime').textContent  = d.uptime  ? 'up ' + d.uptime          : '';
        document.getElementById('cputemp').textContent = d.cputemp ? Math.round(d.cputemp*9/5+32)+'°F' : '';
        document.getElementById('mem').textContent     = d.mem     ? 'mem ' + d.mem + '%'        : '';
        document.getElementById('load').textContent    = d.load    ? 'load ' + d.load            : '';

        var dot = document.getElementById('svc-dot');
        dot.className = 'svc-dot ' + (d.svc_active ? 'up' : 'down');
        document.getElementById('svc-label').textContent =
          d.svc_active ? 'AirPlay ' + fmtAge(d.svc_age_secs) : 'AirPlay DOWN';

        updateHealth(d);
        if (d.logs) updateLogs(d.logs);

        var hs = document.getElementById('history-section');
        var hl = document.getElementById('history-list');
        if (d.history && d.history.length) {
          hs.style.display = 'block';
          hl.innerHTML = d.history.map(function(e) {
            return '<div class="h-row">' +
              '<span class="h-time">'   + esc(e.played_at)         + '</span>' +
              '<span class="h-title">'  + esc(e.title  || '–') + '</span>' +
              '<span class="h-artist">' + (e.artist ? ' \xb7 ' + esc(e.artist) : '') + '</span>' +
              '<span class="h-album">'  + (e.album  ? ' \xb7 ' + esc(e.album)  : '') + '</span>' +
            '</div>';
          }).join('');
        } else {
          hs.style.display = 'none';
        }
      }).catch(function() {});
    }

    poll();
    setInterval(poll, 3000);
  </script>
</body>
</html>"""


def parse_mr_now_playing(raw):
    # AirPlay 2 senders deliver track info as MediaRemote command plists
    # (ssnc/copl) instead of classic core metadata items. Track info sits at
    # plist["params"]["params"] under kMRMediaRemoteNowPlayingInfo* keys.
    try:
        p = plistlib.loads(raw)
    except Exception:
        return None
    if p.get("type") != "updateMRNowPlayingInfo":
        return None
    params = p.get("params")
    inner = params.get("params") if isinstance(params, dict) else None
    if not isinstance(inner, dict) or "kMRMediaRemoteNowPlayingInfoTitle" not in inner:
        return None
    return (inner.get("kMRMediaRemoteNowPlayingInfoTitle"),
            inner.get("kMRMediaRemoteNowPlayingInfoArtist"),
            inner.get("kMRMediaRemoteNowPlayingInfoAlbum"))


def parse_volume(data):
    # "airplay_vol,current_dBFS,min_dBFS,max_dBFS"  airplay_vol: -30..0, -144=muted
    try:
        airplay_vol = float(data.split(",")[0])
        if airplay_vol <= -30:
            return 0
        return round((airplay_vol + 30) / 30 * 100)
    except Exception:
        return None


def _commit_track(title, artist, album):
    # Caller must hold lock. Updates now-playing state and appends to history.
    state["title"]   = title
    state["artist"]  = artist
    state["album"]   = album
    state["playing"] = True
    if title or artist:
        last = history[0] if history else None
        if not last or last["title"] != title or last["artist"] != artist:
            history.appendleft({
                "title":     title,
                "artist":    artist,
                "album":     album,
                "played_at": datetime.now().strftime("%-m/%-d %-I:%M %p"),
            })
            try:
                with open(HISTORY_FILE, "w") as f:
                    json.dump(list(history), f)
            except Exception:
                pass


def handle_item(xml_str):
    try:
        item = ET.fromstring(xml_str)
    except ET.ParseError:
        return
    type_ = item.findtext("type") or ""
    code  = item.findtext("code") or ""
    data_el = item.find("data")
    raw  = None
    data = None
    if data_el is not None and data_el.text:
        try:
            raw  = base64.b64decode(data_el.text)
            data = raw.decode("utf-8", errors="replace")
        except Exception:
            pass

    trace.append({
        "at":   datetime.now().strftime("%H:%M:%S"),
        "type": _hex2ascii(type_),
        "code": _hex2ascii(code),
        "data": data[:60] if data else None,
    })

    copl_info = None
    if type_ == SSNC and code == COPL and raw:
        copl_info = parse_mr_now_playing(raw)

    with lock:
        if type_ == SSNC and code == COPL:
            if copl_info:
                _commit_track(*copl_info)
        elif type_ == SSNC and code == MDST:
            staging.clear()
        elif type_ == SSNC and code == MDEN:
            _commit_track(staging.get("title"), staging.get("artist"),
                          staging.get("album"))
            staging.clear()
        elif code == MINM:
            staging["title"] = data
        elif code == ASAR:
            staging["artist"] = data
        elif code == ASAL:
            staging["album"] = data
        elif type_ == SSNC and code == SNAM and data:
            state["source"] = data
        elif type_ == SSNC and code == PVOL and data:
            state["volume"] = parse_volume(data)
        elif type_ == SSNC and code in (PBEG, PRSM, ABEG):
            state["playing"] = True
        elif type_ == SSNC and code in (PEND, AEND):
            state["playing"] = False
            state["title"]   = None
            state["artist"]  = None
            state["album"]   = None
            state["source"]  = None
            state["volume"]  = None
            staging.clear()
        elif type_ == SSNC and code == PAUS:
            state["playing"] = False


def read_pipe():
    buf = ""
    while True:
        try:
            with open(PIPE, "r", errors="replace") as f:
                while True:
                    chunk = f.read(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while True:
                        s = buf.find("<item>")
                        e = buf.find("</item>")
                        if s == -1 or e == -1 or e < s:
                            break
                        handle_item(buf[s : e + 7])
                        buf = buf[e + 7 :]
        except Exception:
            time.sleep(1)


app = Flask(__name__)


@app.route("/")
def index():
    return HTML


@app.route("/status")
def status():
    svc = get_service_info()
    with lock:
        return jsonify({
            **state,
            "uptime":           get_uptime(),
            "cputemp":          get_cputemp(),
            "mem":              get_mem_pct(),
            "load":             get_load(),
            "svc_active":       svc["active"],
            "svc_restarts":     svc["restarts"],
            "svc_age_secs":     svc["service_age_secs"],
            "logs":             get_recent_logs(),
            "history":          list(history),
        })


@app.route("/debug")
def debug():
    return jsonify(list(trace))


@app.route("/restart_airplay", methods=["POST"])
def restart_airplay():
    try:
        r = subprocess.run(
            ["sudo", "/usr/local/bin/restart-airplay"],
            capture_output=True, text=True, timeout=20
        )
        if r.returncode != 0:
            err = r.stderr.strip() or f"restart-airplay exited {r.returncode}"
            return jsonify({"error": err}), 500
        with lock:
            state["playing"] = False
            state["title"]   = None
            state["artist"]  = None
            state["album"]   = None
            state["source"]  = None
            state["volume"]  = None
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500



if __name__ == "__main__":
    threading.Thread(target=read_pipe, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
