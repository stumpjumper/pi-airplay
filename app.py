#!/usr/bin/env python3
import base64
import threading
import time
import xml.etree.ElementTree as ET
from flask import Flask, jsonify, render_template_string

PIPE = "/tmp/shairport-sync-metadata"

# shairport-sync encodes type/code as hex of the 4-char ASCII code
def _h(s):
    return s.encode().hex()

SSNC  = _h("ssnc")
MINM  = _h("minm")  # track title
ASAR  = _h("asar")  # artist
PBEG  = _h("pbeg")  # play begin
PEND  = _h("pend")  # play end
PAUS  = _h("paus")  # pause
PRSM  = _h("prsm")  # resume
ABEG  = _h("abeg")  # AirPlay 2 session begin
AEND  = _h("aend")  # AirPlay 2 session end
MDST  = _h("mdst")  # metadata block start
MDEN  = _h("mden")  # metadata block end

state   = {"title": None, "artist": None, "playing": False}
staging = {}          # buffered metadata for the in-progress block
lock    = threading.Lock()

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pi AirPlay</title>
  <style>
    body  { font-family: sans-serif; max-width: 480px; margin: 80px auto; padding: 0 20px; color: #222; }
    h1    { font-size: 1rem; font-weight: normal; color: #888; margin: 0 0 24px; }
    #track  { font-size: 1.5rem; font-weight: bold; margin: 0 0 6px; min-height: 1.8rem; }
    #artist { color: #555; margin: 0; min-height: 1.4rem; }
  </style>
</head>
<body>
  <h1 id="status">Loading…</h1>
  <p id="track"></p>
  <p id="artist"></p>
  <script>
    function poll() {
      fetch('/status').then(r => r.json()).then(d => {
        document.getElementById('status').textContent  = d.playing ? 'Now playing' : 'Not playing';
        document.getElementById('track').textContent   = d.title  || '';
        document.getElementById('artist').textContent  = d.artist || '';
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
            # New metadata block starting — reset staging buffer
            staging.clear()
        elif type_ == SSNC and code == MDEN:
            # Metadata block complete — commit atomically
            state["title"]   = staging.get("title")
            state["artist"]  = staging.get("artist")
            state["playing"] = True
            staging.clear()
        elif code == MINM:
            staging["title"] = data
        elif code == ASAR:
            staging["artist"] = data
        elif type_ == SSNC and code in (PBEG, PRSM, ABEG):
            state["playing"] = True
        elif type_ == SSNC and code in (PEND, AEND):
            state["playing"] = False
            state["title"]   = None
            state["artist"]  = None
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
        return jsonify(dict(state))


if __name__ == "__main__":
    threading.Thread(target=read_pipe, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
