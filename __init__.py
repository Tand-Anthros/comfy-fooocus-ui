# Fooocus-style UI for ComfyUI
# Copyright (C) 2026 fooocus-ui contributors
# SPDX-License-Identifier: GPL-3.0-or-later
#
# The interface this serves follows Fooocus by lllyasviel (https://github.com/lllyasviel/Fooocus, GPL-3.0).
# No Fooocus source code is included.
"""Fooocus-style page for ComfyUI.  Open  http://<comfyui address>:8188/fooocus

Adds no nodes. It makes ComfyUI hand out the page and gives the page what a browser cannot do itself:

  /fooocus                  the page (no CORS flag or extra web server needed)
  /fooocus/styles.json      all styles from  styles/*.json  in this folder (Fooocus format: name, prompt, negative_prompt)
  /fooocus/samples/<file>   style preview pictures from  styles/samples/
  /fooocus/history          every image in the ComfyUI output folder, so the History Log survives restarts
"""
import asyncio
import json
import logging
import os

from aiohttp import web

import folder_paths
from server import PromptServer

_DIR = os.path.dirname(os.path.abspath(__file__))
_PAGE = os.path.join(_DIR, "fooocus-comfy.html")
_STYLES = os.path.join(_DIR, "styles")
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
# same order as Fooocus; any other *.json files follow alphabetically
_FIRST = ["sdxl_styles_fooocus.json", "sdxl_styles_sai.json", "sdxl_styles_mre.json",
          "sdxl_styles_twri.json", "sdxl_styles_diva.json", "sdxl_styles_marc_k3nt3l.json"]

routes = PromptServer.instance.routes


@routes.get("/fooocus")
async def fooocus_page(request):
    if not os.path.isfile(_PAGE):
        return web.Response(status=404, text="fooocus-comfy.html is missing next to this __init__.py")
    return web.FileResponse(_PAGE, headers={"Cache-Control": "no-cache"})


@routes.get("/fooocus/styles.json")
async def fooocus_styles(request):
    files = [f for f in os.listdir(_STYLES) if f.lower().endswith(".json")] if os.path.isdir(_STYLES) else []
    files.sort(key=lambda f: (_FIRST.index(f) if f in _FIRST else len(_FIRST), f.lower()))
    styles = []
    for name in files:
        try:
            with open(os.path.join(_STYLES, name), encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:  # one broken file should not hide the others
            logging.warning("[fooocus-ui] cannot read styles file %s: %s", name, exc)
            continue
        if isinstance(data, list):
            styles.extend(s for s in data if isinstance(s, dict) and s.get("name"))
    if not styles:
        return web.json_response([], status=404)
    return web.json_response(styles)


@routes.get("/fooocus/samples/{name}")
async def fooocus_sample(request):
    name = os.path.basename(request.match_info["name"])
    path = os.path.join(_STYLES, "samples", name)
    if not name.lower().endswith(_IMAGE_EXT) or not os.path.isfile(path):
        return web.Response(status=404)
    return web.FileResponse(path, headers={"Cache-Control": "max-age=86400"})


def _text_chunks(path):
    """Generation parameters stored in the image file (PNG text chunks)."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            info = dict(im.info)
        return {k: info[k] for k in ("fooocus", "prompt", "parameters") if isinstance(info.get(k), str)}
    except Exception:
        return {}


def _scan_output(t_from, t_to):
    out_dir = folder_paths.get_output_directory()
    items = []
    for root, dirs, files in os.walk(out_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if not name.lower().endswith(_IMAGE_EXT):
                continue
            path = os.path.join(root, name)
            try:
                t = int(os.path.getmtime(path) * 1000)
            except OSError:
                continue
            if t_from is not None and not (t_from <= t < t_to):
                continue
            sub = os.path.relpath(root, out_dir).replace("\\", "/")
            item = {"filename": name, "subfolder": "" if sub == "." else sub, "t": t}
            if t_from is not None:
                item["chunks"] = _text_chunks(path)
            items.append(item)
    return items


@routes.get("/fooocus/history")
async def fooocus_history(request):
    """Without parameters: every output image (name, subfolder, time in ms).
    With ?from=<ms>&to=<ms>: only that time range, with the parameters stored in each file."""
    try:
        t_from = int(request.query["from"]) if "from" in request.query else None
        t_to = int(request.query.get("to", "0")) if t_from is not None else None
    except ValueError:
        return web.json_response({"error": "from/to must be numbers"}, status=400)
    items = await asyncio.get_running_loop().run_in_executor(None, _scan_output, t_from, t_to)
    return web.json_response({"items": items})


NODE_CLASS_MAPPINGS = {}
__all__ = ["NODE_CLASS_MAPPINGS"]
