#!/usr/bin/env python3
import base64
import json
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime
from flask import Flask, jsonify, render_template_string, request

PIPE           = "/tmp/shairport-sync-metadata"
HISTORY_FILE   = "/home/aal/pi-airplay/history.json"
SHAIRPORT_CONF = "/etc/shairport-sync.conf"

OUTPUT_DEVICES = {
    "headphones": "hw:Headphones",
    "hdmi":       "hw:vc4hdmi",
}

def _h(s):
    return s.encode().hex()

SSNC  = _h("ssnc")
MINM  = _h("minm")
ASAR  = _h("asar")
ASAL  = _h("asal")  # album
PBEG  = _h("pbeg")
PEND  = _h("pend")
PAUS  = _h("paus")
PRSM  = _h("prsm")
ABEG  = _h("abeg")
AEND  = _h("aend")
MDST  = _h("mdst")
MDEN  = _h("mden")
SNAM  = _h("snam")  # source device name
PVOL  = _h("pvol")  # volume


def get_current_output():
    try:
        with open(SHAIRPORT_CONF) as f:
            for line in f:
                if "output_device" in line:
                    return "hdmi" if "vc4hdmi" in line else "headphones"
    except Exception:
        pass
    return "headphones"


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


def get_shairport_active():
    try:
        r = subprocess.run(["systemctl", "is-active", "shairport-sync"],
                           capture_output=True, text=True, timeout=3)
        return r.stdout.strip() == "active"
    except Exception:
        return None


state   = {"title": None, "artist": None, "album": None,
           "playing": False, "source": None, "volume": None,
           "output": get_current_output()}
staging = {}


def _load_history():
    try:
        with open(HISTORY_FILE) as f:
            return deque(json.load(f), maxlen=25)
    except Exception:
        return deque(maxlen=25)

history = _load_history()
lock    = threading.Lock()

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pi AirPlay</title>
  <style>
    * { box-sizing: border-box; }
    body  { font-family: sans-serif; max-width: 520px; margin: 60px auto;
            padding: 0 24px; color: #222; }
    #status { font-size: .85rem; font-weight: normal; color: #888;
              margin: 0 0 20px; letter-spacing: .04em; text-transform: uppercase; }
    #track  { font-size: 1.5rem; font-weight: bold; margin: 0 0 4px; min-height: 1.8rem; }
    #artist { font-size: 1rem; color: #444; margin: 0 0 2px; min-height: 1.2rem; }
    #album  { font-size: .9rem; color: #888; font-style: italic;
              margin: 0 0 20px; min-height: 1.1rem; }
    .meta-row { display: flex; align-items: center; gap: 16px;
                font-size: .85rem; color: #666; margin-bottom: 6px; }
    #source { flex: 1; white-space: nowrap; overflow: hidden;
              text-overflow: ellipsis; min-height: 1rem; }
    .vol-wrap { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
    .vol-bar  { width: 80px; height: 4px; background: #ddd; border-radius: 2px; }
    .vol-fill { height: 100%; background: #888; border-radius: 2px;
                transition: width .4s; }
    #vol-pct  { width: 2.5em; text-align: right; }
    hr { border: none; border-top: 1px solid #eee; margin: 20px 0; }
    #sysinfo  { font-size: .8rem; color: #aaa; display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 16px; }
    .svc-dot  { display: inline-block; width: 7px; height: 7px; border-radius: 50%;
                margin-right: 4px; vertical-align: middle; background: #ccc; }
    .svc-dot.up   { background: #5cb85c; }
    .svc-dot.down { background: #d9534f; }
    .out-sel  { display: flex; gap: 8px; margin-bottom: 20px; }
    .out-btn  { font-size: .8rem; padding: 4px 14px; border: 1px solid #ddd;
                border-radius: 12px; background: none; cursor: pointer; color: #aaa; }
    .out-btn.active { background: #333; color: #fff; border-color: #333; }
    #history-section h2 { font-size: .75rem; font-weight: normal; color: #aaa;
                          letter-spacing: .06em; text-transform: uppercase; margin: 0 0 10px; }
    .h-row    { display: flex; gap: 12px; align-items: baseline;
                padding: 6px 0; border-bottom: 1px solid #f0f0f0; font-size: .85rem; }
    .h-row:last-child { border-bottom: none; }
    .h-time   { color: #bbb; font-size: .75rem; white-space: nowrap; flex-shrink: 0; width: 5.5em; }
    .h-title  { font-weight: 500; }
    .h-artist { color: #888; }
    .h-album  { color: #bbb; font-size: .78rem; font-style: italic; }
  </style>
</head>
<body>
  <h1 id="status">Loading...</h1>
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
  <div id="sysinfo">
    <span id="uptime"></span>
    <span id="cputemp"></span>
    <span id="mem"></span>
    <span id="load"></span>
    <span id="airplay-svc"><span class="svc-dot" id="airplay-dot"></span>AirPlay</span>
  </div>
  <div class="out-sel">
    <button class="out-btn" id="btn-headphones" onclick="setOutput('headphones')">Headphones</button>
    <button class="out-btn" id="btn-hdmi" onclick="setOutput('hdmi')">HDMI</button>
  </div>
  <div id="history-section" style="display:none">
    <h2>Recent plays</h2>
    <div id="history-list"></div>
  </div>
  <script>
    function setOutput(dev) {
      fetch('/set_output', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({device: dev})
      }).then(function(r) { return r.json(); })
        .then(function(d) { if (d.output) markOutput(d.output); })
        .catch(function() {});
    }
    function markOutput(dev) {
      ['headphones', 'hdmi'].forEach(function(id) {
        var el = document.getElementById('btn-' + id);
        el.className = 'out-btn' + (id === dev ? ' active' : '');
      });
    }
    function poll() {
      fetch('/status').then(function(r) { return r.json(); }).then(function(d) {
        document.getElementById('status').textContent  = d.playing ? 'Now playing' : 'Not playing';
        document.getElementById('track').textContent   = d.title  || '';
        document.getElementById('artist').textContent  = d.artist || '';
        document.getElementById('album').textContent   = d.album  || '';
        document.getElementById('source').textContent  = d.source ? '> ' + d.source : '';

        var vw = document.getElementById('vol-wrap');
        if (d.volume !== null && d.volume !== undefined) {
          vw.style.display = 'flex';
          document.getElementById('vol-fill').style.width = d.volume + '%';
          document.getElementById('vol-pct').textContent  = d.volume + '%';
        } else {
          vw.style.display = 'none';
        }

        document.getElementById('uptime').textContent  = d.uptime  ? 'Uptime ' + d.uptime      : '';
        document.getElementById('cputemp').textContent = d.cputemp ? 'CPU ' + d.cputemp + '\xb0C' : '';
        document.getElementById('mem').textContent     = d.mem     ? 'Mem ' + d.mem + '%'         : '';
        document.getElementById('load').textContent    = d.load    ? 'Load ' + d.load             : '';

        var dot = document.getElementById('airplay-dot');
        if (d.shairport_active !== null && d.shairport_active !== undefined) {
          dot.className = 'svc-dot ' + (d.shairport_active ? 'up' : 'down');
        }

        if (d.output) markOutput(d.output);

        var hs = document.getElementById('history-section');
        var hl = document.getElementById('history-list');
        if (d.history && d.history.length) {
          hs.style.display = 'block';
          hl.innerHTML = d.history.map(function(e) {
            return '<div class="h-row">' +
              '<span class="h-time">'   + e.played_at + '</span>' +
              '<span class="h-title">'  + (e.title  || '-') + '</span>' +
              '<span class="h-artist">' + (e.artist ? ' \xb7 ' + e.artist : '') + '</span>' +
              '<span class="h-album">'  + (e.album  ? ' \xb7 ' + e.album  : '') + '</span>' +
            '</div>';
          }).join('');
        } else {
          hs.style.display = 'none';
        }
      }).catch(function() {});
    }
    poll();
    setInterval(poll, 2000);
  </script>
</body>
</html>"""


def b64decode(text):
    try:
        return base64.b64decode(text).decode("utf-8", errors="replace")
    except Exception:
        return None


def parse_volume(data):
    # "airplay_vol,current_dBFS,min_dBFS,max_dBFS"  airplay_vol: -30..0, -144=muted
    try:
        airplay_vol = float(data.split(",")[0])
        if airplay_vol <= -30:
            return 0
        return round((airplay_vol + 30) / 30 * 100)
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


def handle_item(xml_str):
    try:
        item = ET.fromstring(xml_str)
    except ET.ParseError:
        return
    type_ = item.findtext("type") or ""
    code  = item.findtext("code") or ""
    data_el = item.find("data")
    data = b64decode(data_el.text) if data_el is not None and data_el.text else None

    with lock:
        if type_ == SSNC and code == MDST:
            staging.clear()
        elif type_ == SSNC and code == MDEN:
            title  = staging.get("title")
            artist = staging.get("artist")
            album  = staging.get("album")
            state["title"]   = title
            state["artist"]  = artist
            state["album"]   = album
            state["playing"] = True
            staging.clear()
            if title or artist:
                last = history[0] if history else None
                if not last or last["title"] != title or last["artist"] != artist:
                    history.appendleft({
                        "title":     title,
                        "artist":    artist,
                        "album":     album,
                        "played_at": datetime.now().strftime("%-I:%M %p"),
                    })
                    try:
                        with open(HISTORY_FILE, "w") as f:
                            json.dump(list(history), f)
                    except Exception:
                        pass
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
    return render_template_string(HTML)


@app.route("/status")
def status():
    with lock:
        return jsonify({**state,
                        "uptime":           get_uptime(),
                        "cputemp":          get_cputemp(),
                        "mem":              get_mem_pct(),
                        "load":             get_load(),
                        "shairport_active": get_shairport_active(),
                        "history":          list(history)})


@app.route("/set_output", methods=["POST"])
def set_output():
    device = (request.json or {}).get("device")
    if device not in OUTPUT_DEVICES:
        return jsonify({"error": "invalid device"}), 400
    result = subprocess.run(
        ["sudo", "/usr/local/bin/set-airplay-output", OUTPUT_DEVICES[device]],
        capture_output=True, timeout=15
    )
    if result.returncode != 0:
        return jsonify({"error": "failed to switch output"}), 500
    with lock:
        state["output"] = device
    return jsonify({"output": device})


if __name__ == "__main__":
    threading.Thread(target=read_pipe, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
