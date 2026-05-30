#!/usr/bin/env python3
import base64
import threading
import time
import xml.etree.ElementTree as ET
from flask import Flask, jsonify, render_template_string

PIPE = "/tmp/shairport-sync-metadata"

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

state   = {"title": None, "artist": None, "album": None,
           "playing": False, "source": None, "volume": None}
staging = {}
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
    #sysinfo  { font-size: .8rem; color: #aaa; display: flex; gap: 16px; }
  </style>
</head>
<body>
  <h1 id="status">Loading…</h1>
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
  </div>
  <script>
    function poll() {
      fetch('/status').then(r => r.json()).then(d => {
        document.getElementById('status').textContent  = d.playing ? 'Now playing' : 'Not playing';
        document.getElementById('track').textContent   = d.title  || '';
        document.getElementById('artist').textContent  = d.artist || '';
        document.getElementById('album').textContent   = d.album  || '';
        document.getElementById('source').textContent  = d.source ? '▶ ' + d.source : '';

        var vw = document.getElementById('vol-wrap');
        if (d.volume !== null && d.volume !== undefined) {
          vw.style.display = 'flex';
          document.getElementById('vol-fill').style.width = d.volume + '%';
          document.getElementById('vol-pct').textContent  = d.volume + '%';
        } else {
          vw.style.display = 'none';
        }

        document.getElementById('uptime').textContent  = d.uptime  ? 'Uptime ' + d.uptime  : '';
        document.getElementById('cputemp').textContent = d.cputemp ? 'CPU '    + d.cputemp + '°C' : '';
      }).catch(() => {});
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
            state["title"]  = staging.get("title")
            state["artist"] = staging.get("artist")
            state["album"]  = staging.get("album")
            state["playing"] = True
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
    return render_template_string(HTML)


@app.route("/status")
def status():
    with lock:
        return jsonify({**state, "uptime": get_uptime(), "cputemp": get_cputemp()})


if __name__ == "__main__":
    threading.Thread(target=read_pipe, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
