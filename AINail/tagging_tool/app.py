#!/usr/bin/env python3
"""
Nail Mask Tagging Tool
Usage: python app.py --control <dir> --target <dir> --mask <dir>
Then open http://localhost:5000
"""
import os, glob, base64, argparse
from flask import Flask, send_file, jsonify, request, render_template, abort

parser = argparse.ArgumentParser()
parser.add_argument("-c", "--control", required=True, help="original hand images folder")
parser.add_argument("-t", "--target",  required=True, help="hand + nail art images folder")
parser.add_argument("-m", "--mask",    required=True, help="model output src_sized_mask folder")
args = parser.parse_args()

app = Flask(__name__)

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONTROL_DIR = os.path.abspath(args.control)
TARGET_DIR  = os.path.abspath(args.target)
MASK_DIR    = os.path.abspath(args.mask)
SAVE_DIR    = os.path.join(BASE_DIR, "tagged_masks")

os.makedirs(SAVE_DIR, exist_ok=True)

EXTS = (".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG")


def _find(folder, stem):
    for ext in EXTS:
        p = os.path.join(folder, stem + ext)
        if os.path.exists(p):
            return p
    return None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/images")
def list_images():
    if not os.path.isdir(TARGET_DIR):
        return jsonify([])
    files = []
    for ext in EXTS:
        files.extend(glob.glob(os.path.join(TARGET_DIR, f"*{ext}")))
    stems = sorted({os.path.splitext(os.path.basename(f))[0] for f in files})
    return jsonify(stems)


@app.route("/api/image/control/<stem>")
def serve_control(stem):
    p = _find(CONTROL_DIR, stem)
    return send_file(p) if p else abort(404)


@app.route("/api/image/target/<stem>")
def serve_target(stem):
    p = _find(TARGET_DIR, stem)
    return send_file(p) if p else abort(404)


@app.route("/api/mask/<stem>/<int:finger>")
def get_mask(stem, finger):
    if not (0 <= finger < 5):
        abort(404)
    # user-edited mask takes priority over model output
    saved = os.path.join(SAVE_DIR, f"{stem}_mask_{finger}.png")
    if os.path.exists(saved):
        return send_file(saved)
    mp = os.path.join(MASK_DIR, f"{stem}_mask_{finger}.png")
    if os.path.exists(mp):
        return send_file(mp)
    abort(404)


@app.route("/api/mask/<stem>/<int:finger>", methods=["POST"])
def save_mask(stem, finger):
    if not (0 <= finger < 5):
        return jsonify({"ok": False, "error": "finger index out of range"}), 400
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "Invalid or missing JSON payload"}), 400
    raw = payload.get("image", "")
    if not isinstance(raw, str) or not raw:
        return jsonify({"ok": False, "error": "Missing image in JSON payload"}), 400
    if not raw.startswith("data:image/png;base64,"):
        return jsonify({"ok": False, "error": "Invalid data URL prefix"}), 400
    b64 = raw.split(",", 1)[-1]
    try:
        img_bytes = base64.b64decode(b64, validate=True)
    except Exception:
        return jsonify({"ok": False, "error": "Malformed base64 data"}), 400
    out = os.path.join(SAVE_DIR, f"{stem}_mask_{finger}.png")
    with open(out, "wb") as f:
        f.write(img_bytes)
    return jsonify({"ok": True})


@app.route("/api/mask_status/<stem>")
def mask_status(stem):
    return jsonify([
        {
            "saved": os.path.exists(os.path.join(SAVE_DIR,  f"{stem}_mask_{i}.png")),
            "model": os.path.exists(os.path.join(MASK_DIR,  f"{stem}_mask_{i}.png")),
        }
        for i in range(5)
    ])


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
