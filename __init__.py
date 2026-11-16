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
  /fooocus/history          every image in the ComfyUI output folder with its parameters (History Log); delete one image
  /fooocus/thumb            a small preview of an output image
  /fooocus/tags             booru tag lists from  tags/*.csv|*.txt  plus the personal dictionary (tag correctness check)
  /fooocus/lora             add or delete a LoRA file (.safetensors) in models/loras
"""
import asyncio
import gc
import io
import json
import logging
import os
import stat
import urllib.request

from aiohttp import web

import folder_paths
from server import PromptServer

_DIR = os.path.dirname(os.path.abspath(__file__))
_PAGE = os.path.join(_DIR, "fooocus-comfy.html")
_STYLES = os.path.join(_DIR, "styles")
_TAGS = os.path.join(_DIR, "tags")
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
# same order as Fooocus; any other *.json files follow alphabetically
_FIRST = ["sdxl_styles_fooocus.json", "sdxl_styles_sai.json", "sdxl_styles_mre.json",
          "sdxl_styles_twri.json", "sdxl_styles_diva.json", "sdxl_styles_marc_k3nt3l.json"]

# Tag correctness check: the list offered for download, and the personal dictionary ("patch") in the ComfyUI folder.
# Tag lists the page offers to download into tags/: file name -> where it comes from.
# Every list is a CSV whose first column is the tag. Add an entry here to offer another list.
_TAG_SOURCES = {
    "danbooru.csv": "https://raw.githubusercontent.com/DominikDoom/a1111-sd-webui-tagcomplete/main/tags/danbooru.csv",
    "e621.csv": "https://raw.githubusercontent.com/DominikDoom/a1111-sd-webui-tagcomplete/main/tags/e621.csv",
    "gelbooru.csv": "https://raw.githubusercontent.com/DraconicDragon/dbr-e621-lists-archive/main/tag-lists/gelbooru/gelbooru_2026-06-10_pt20.csv",
}
_PATCH = os.path.join(getattr(folder_paths, "base_path", os.getcwd()), "fooocus-ui-tags.json")
# quality and rating words that anime models are trained with but that are not booru tags
_PATCH_DEFAULT = {"add": ["masterpiece", "best_quality", "good_quality", "normal_quality", "low_quality", "worst_quality",
                          "very_aesthetic", "aesthetic", "displeasing", "very_displeasing",
                          "newest", "recent", "mid", "early", "old",
                          "safe", "sensitive", "nsfw", "explicit", "general", "questionable"],
                  "remove": []}

routes = PromptServer.instance.routes


def _run(fn, *args):
    return asyncio.get_running_loop().run_in_executor(None, fn, *args)


# ---------------------------------------------------------------- page and styles

@routes.get("/fooocus")
async def fooocus_page(request):
    if not os.path.isfile(_PAGE):
        return web.Response(status=404, text="fooocus-comfy.html is missing next to this __init__.py")
    return web.FileResponse(_PAGE, headers={"Cache-Control": "no-cache"})


@routes.get("/fooocus/ping")
async def fooocus_ping(request):
    return web.json_response({"ok": True})


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


# ---------------------------------------------------------------- history

_TEXT_KEYS = ("text", "text_g", "text_l", "prompt")
_META_CACHE = {}  # path -> (mtime_ns, size, entry); parameters are read from each file only once


def _is_link(v):
    return isinstance(v, list) and len(v) == 2 and isinstance(v[1], int)


def _trace(graph, link, via, test):
    """Follow a link upstream through the inputs named in `via` until a node passes `test`."""
    seen = set()
    while _is_link(link):
        node_id = str(link[0])
        if node_id in seen or not isinstance(graph.get(node_id), dict):
            return None
        seen.add(node_id)
        inputs = graph[node_id].get("inputs") or {}
        if test(inputs):
            return node_id
        link = next((inputs[k] for k in via if _is_link(inputs.get(k))), None)
    return None


def _meta_from_graph(graph):
    """Prompt and sampler settings from a ComfyUI API graph (images not made by this UI)."""
    nodes = {k: v for k, v in graph.items() if isinstance(v, dict) and isinstance(v.get("inputs"), dict)}
    sampler = next((n["inputs"] for n in nodes.values() if n.get("class_type") in ("KSampler", "KSamplerAdvanced")), None)
    if sampler is None:
        return {}

    def text_of(key):
        node_id = _trace(nodes, sampler.get(key), ("conditioning", key, "conditioning_1", "conditioning_to"),
                         lambda inputs: any(k in inputs for k in _TEXT_KEYS))
        if node_id is None:
            return None, None
        return node_id, next((nodes[node_id]["inputs"][k] for k in _TEXT_KEYS if isinstance(nodes[node_id]["inputs"].get(k), str)), "")

    pos_id, pos = text_of("positive")
    neg_id, neg = text_of("negative")
    files = lambda key: ", ".join(n["inputs"][key] for n in nodes.values() if isinstance(n["inputs"].get(key), str))
    seed = sampler.get("seed", sampler.get("noise_seed"))
    meta = {"Source": "ComfyUI", "Prompt": pos, "Negative Prompt": None if neg_id == pos_id else neg,
            "Steps": sampler.get("steps"), "Guidance Scale": sampler.get("cfg"), "Sampler": sampler.get("sampler_name"),
            "Scheduler": sampler.get("scheduler"), "Seed": str(seed) if isinstance(seed, int) else None,
            "Base Model": files("ckpt_name") or files("unet_name"), "LoRAs": files("lora_name")}
    return {k: v for k, v in meta.items() if isinstance(v, (str, int, float)) and v != ""}


def _read_meta(path):
    """Parameters stored in the image file. Returns ("meta", dict) or ("chunks", {"parameters": text}) or None."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            info = dict(im.info)
    except Exception:
        return None
    for key in ("fooocus", "prompt"):
        if isinstance(info.get(key), str):
            try:
                data = json.loads(info[key])
            except ValueError:
                continue
            if isinstance(data, dict):
                meta = data if key == "fooocus" else _meta_from_graph(data)
                if meta:
                    return "meta", meta
    if isinstance(info.get("parameters"), str):
        return "chunks", {"parameters": info["parameters"]}
    return None


def _scan_output():
    out_dir = folder_paths.get_output_directory()
    items, alive = [], set()
    for root, dirs, files in os.walk(out_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if not name.lower().endswith(_IMAGE_EXT):
                continue
            path = os.path.join(root, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            alive.add(path)
            cached = _META_CACHE.get(path)
            if cached is None or cached[0] != st.st_mtime_ns or cached[1] != st.st_size:
                sub = os.path.relpath(root, out_dir).replace("\\", "/")
                entry = {"filename": name, "subfolder": "" if sub == "." else sub, "t": int(st.st_mtime * 1000)}
                found = _read_meta(path)
                if found:
                    entry[found[0]] = found[1]
                cached = _META_CACHE[path] = (st.st_mtime_ns, st.st_size, entry)
            items.append(cached[2])
    for path in set(_META_CACHE) - alive:
        del _META_CACHE[path]
    return items


def _output_image(subfolder, filename):
    """Full path of an image inside the output folder, or None when the name points anywhere else."""
    out_dir = os.path.realpath(folder_paths.get_output_directory())
    path = os.path.realpath(os.path.join(out_dir, str(subfolder or ""), str(filename)))
    try:
        inside = os.path.commonpath([out_dir, path]) == out_dir and path != out_dir
    except ValueError:  # different drives on Windows
        inside = False
    return path if inside and path.lower().endswith(_IMAGE_EXT) else None


_THUMBS = {}  # (path, mtime_ns, size) -> JPEG bytes, oldest dropped first
_THUMBS_MAX = 400


def _make_thumb(path, size):
    key = (path, os.stat(path).st_mtime_ns, size)
    data = _THUMBS.get(key)
    if data is None:
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((size, size))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=85)
        data = _THUMBS[key] = buf.getvalue()
        while len(_THUMBS) > _THUMBS_MAX:
            del _THUMBS[next(iter(_THUMBS))]
    return data


@routes.get("/fooocus/thumb")
async def fooocus_thumb(request):
    """Small JPEG preview of an output image: ?filename=...&subfolder=...&size=<longest side, 64-1024>"""
    path = _output_image(request.query.get("subfolder", ""), request.query.get("filename", ""))
    if path is None or not os.path.isfile(path):
        return web.Response(status=404)
    try:
        size = max(64, min(1024, int(request.query.get("size", "640"))))
        data = await _run(_make_thumb, path, size)
    except Exception as exc:
        logging.warning("[fooocus-ui] cannot make a preview of %s: %s", path, exc)
        return web.Response(status=500)
    return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


@routes.get("/fooocus/history")
async def fooocus_history(request):
    """Every image in the output folder: name, subfolder, time (ms) and the parameters stored in the file."""
    return web.json_response({"items": await _run(_scan_output)})


@routes.post("/fooocus/history/delete")
async def fooocus_history_delete(request):
    """Delete one image file from the output folder. Body: {"filename": ..., "subfolder": ...}"""
    try:
        body = await request.json()
        filename, subfolder = str(body["filename"]), str(body.get("subfolder") or "")
    except Exception:
        return web.json_response({"error": "expected JSON with filename and subfolder"}, status=400)
    path = _output_image(subfolder, filename)
    if path is None:
        return web.json_response({"error": "not an image in the output folder"}, status=400)
    if not os.path.isfile(path):
        return web.json_response({"error": "file not found"}, status=404)
    try:
        os.remove(path)
    except OSError as exc:
        return web.json_response({"error": str(exc)}, status=500)
    return web.json_response({"deleted": filename})


# ---------------------------------------------------------------- tag correctness check

_TAG_CACHE = {"key": None, "tags": []}


def _norm_tag(tag):
    return "_".join(str(tag).lower().replace("\\(", "(").replace("\\)", ")").replace("_", " ").split())


def _load_tags():
    """All tags from tags/*.csv (first column) and tags/*.txt (one per line), re-read only when the files change."""
    files = sorted(f for f in os.listdir(_TAGS) if f.lower().endswith((".csv", ".txt"))) if os.path.isdir(_TAGS) else []
    key = tuple((f, os.path.getmtime(os.path.join(_TAGS, f))) for f in files)
    if key != _TAG_CACHE["key"]:
        tags = set()
        for name in files:
            try:
                with open(os.path.join(_TAGS, name), encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        tag = _norm_tag(line.split(",", 1)[0].strip().strip('"'))
                        if tag:
                            tags.add(tag)
            except OSError as exc:
                logging.warning("[fooocus-ui] cannot read tag file %s: %s", name, exc)
        _TAG_CACHE.update(key=key, tags=sorted(tags))
    return _TAG_CACHE["tags"]


def _unique(tags):
    """The tags without empties and repeats, first occurrence kept."""
    return list(dict.fromkeys(t for t in tags if t))


def _read_patch():
    """The personal dictionary; the file is created with a few common quality words when it does not exist."""
    try:
        with open(_PATCH, encoding="utf-8") as fh:
            data = json.load(fh)
        return {key: _unique(_norm_tag(t) for t in data.get(key, [])) for key in ("add", "remove")}
    except FileNotFoundError:
        _write_patch(_PATCH_DEFAULT)
        return {k: list(v) for k, v in _PATCH_DEFAULT.items()}
    except Exception as exc:
        logging.warning("[fooocus-ui] cannot read %s: %s", _PATCH, exc)
        return {"add": [], "remove": []}


def _write_patch(patch):
    with open(_PATCH, "w", encoding="utf-8") as fh:
        json.dump(patch, fh, ensure_ascii=False, indent=1)


def _tags_payload():
    tags = _load_tags()
    files = sorted(f for f in os.listdir(_TAGS) if f.lower().endswith((".csv", ".txt"))) if os.path.isdir(_TAGS) else []
    return {"tags": "\n".join(tags), "count": len(tags), "files": files, "sources": _TAG_SOURCES,
            "patch": _read_patch(), "patch_file": _PATCH}


@routes.get("/fooocus/tags")
async def fooocus_tags(request):
    return web.json_response(await _run(_tags_payload))


@routes.post("/fooocus/tags/patch")
async def fooocus_tags_patch(request):
    """Body: {"action": "add" | "remove", "tag": ...}. "add" makes a tag known, "remove" makes it unknown."""
    try:
        body = await request.json()
        action, tag = body["action"], _norm_tag(body["tag"])
    except Exception:
        return web.json_response({"error": "expected JSON with action and tag"}, status=400)
    if action not in ("add", "remove") or not tag:
        return web.json_response({"error": "action must be add or remove, tag must not be empty"}, status=400)
    patch, base = _read_patch(), set(await _run(_load_tags))
    # every tag is kept once: nothing is added that the tag lists already know, nothing is removed that nobody knows
    if action == "add":
        known = tag not in patch["remove"] and (tag in base or tag in patch["add"])
        patch["remove"] = [t for t in patch["remove"] if t != tag]
        if tag not in base and tag not in patch["add"]:
            patch["add"].append(tag)
        result = "exists" if known else "added"
    else:
        known = tag not in patch["remove"] and (tag in base or tag in patch["add"])
        patch["add"] = [t for t in patch["add"] if t != tag]
        if tag in base and tag not in patch["remove"]:
            patch["remove"].append(tag)
        result = "removed" if known else "absent"
    try:
        _write_patch(patch)
    except OSError as exc:
        return web.json_response({"error": f"cannot write {_PATCH}: {exc}"}, status=500)
    return web.json_response({"patch": patch, "patch_file": _PATCH, "result": result})


def _download_tags(name):
    os.makedirs(_TAGS, exist_ok=True)
    with urllib.request.urlopen(_TAG_SOURCES[name], timeout=120) as resp:
        data = resp.read()
    with open(os.path.join(_TAGS, name), "wb") as fh:
        fh.write(data)
    return len(_load_tags())


@routes.post("/fooocus/tags/download")
async def fooocus_tags_download(request):
    """Fetch one of the known tag lists into tags/. Body: {"name": <a key of _TAG_SOURCES>}"""
    try:
        name = (await request.json()).get("name")
    except Exception:
        name = None
    if name not in _TAG_SOURCES:
        return web.json_response({"error": "unknown tag list"}, status=400)
    try:
        count = await _run(_download_tags, name)
    except Exception as exc:
        return web.json_response({"error": f"download failed: {exc}"}, status=502)
    return web.json_response({"count": count})


@routes.post("/fooocus/tags/upload")
async def fooocus_tags_upload(request):
    """Store a known tag list sent by the browser (used when the server itself cannot download it): ?name=<a key of _TAG_SOURCES>"""
    name = request.query.get("name")
    if name not in _TAG_SOURCES:
        return web.json_response({"error": "unknown tag list"}, status=400)
    data = await request.read()
    if not data or b"," not in data[:4096] and b"\n" not in data[:4096]:
        return web.json_response({"error": "expected the text of a tag list"}, status=400)
    try:
        os.makedirs(_TAGS, exist_ok=True)
        with open(os.path.join(_TAGS, name), "wb") as fh:
            fh.write(data)
        count = len(await _run(_load_tags))
    except OSError as exc:
        return web.json_response({"error": str(exc)}, status=500)
    return web.json_response({"count": count})


# ---------------------------------------------------------------- LoRA files

_LORA_EXT = ".safetensors"  # the only format accepted: it cannot carry code


def _lora_roots():
    return [os.path.realpath(p) for p in folder_paths.get_folder_paths("loras")]


def _lora_target(folder, name):
    """Where a new LoRA goes: <loras root>/<folder>/<name>.safetensors. `folder` is one existing subfolder or empty."""
    folder = os.path.basename(str(folder or "").replace("\\", "/").strip("/ "))
    name = os.path.basename(str(name or "").replace("\\", "/")).strip()
    if name.lower().endswith(_LORA_EXT):
        name = name[:-len(_LORA_EXT)]
    name = name.strip(" .")
    if not name or folder in (".", ".."):
        return None
    roots = _lora_roots()
    root = next((r for r in roots if folder and os.path.isdir(os.path.join(r, folder))), None)
    if root is None:
        root, folder = roots[0], ""
    return os.path.join(root, folder, name + _LORA_EXT)


@routes.post("/fooocus/lora/upload")
async def fooocus_lora_upload(request):
    """The request body is the file itself: ?name=<LoRA name>&dir=<subfolder of models/loras or empty>"""
    target = _lora_target(request.query.get("dir"), request.query.get("name"))
    if target is None:
        return web.json_response({"error": "a LoRA name is required"}, status=400)
    tmp, size, head = target + ".part", 0, b""
    try:
        with open(tmp, "wb") as fh:  # streamed to disk, so the size is not limited by ComfyUI's upload limit
            async for chunk in request.content.iter_chunked(1 << 20):
                if size < 9:
                    head = (head + chunk)[:9]
                fh.write(chunk)
                size += len(chunk)
        # a safetensors file starts with the 8-byte length of its JSON header, then "{"
        if size < 16 or head[8:9] != b"{" or int.from_bytes(head[:8], "little") > size:
            os.remove(tmp)
            return web.json_response({"error": "this is not a .safetensors file"}, status=400)
        os.replace(tmp, target)
    except OSError as exc:
        if os.path.exists(tmp):
            os.remove(tmp)
        return web.json_response({"error": str(exc)}, status=500)
    return web.json_response({"name": os.path.basename(target), "size": size})


async def _remove_file(path):
    """Delete a file; returns an error text or None.

    On Windows a LoRA that was used since ComfyUI started stays mapped into memory (and a file may be read-only),
    which makes deleting fail with "access denied". So: clear the read-only flag, ask ComfyUI to drop its caches
    and loaded models, and try again a few times."""
    last = None
    for attempt in range(6):
        try:
            os.remove(path)
            return None
        except FileNotFoundError:
            return None
        except PermissionError as exc:
            last = exc
        except OSError as exc:
            return str(exc)
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass
        if attempt == 0:
            try:
                queue = PromptServer.instance.prompt_queue
                queue.set_flag("unload_models", True)
                queue.set_flag("free_memory", True)
            except Exception as exc:
                logging.warning("[fooocus-ui] cannot ask ComfyUI to free memory: %s", exc)
        await asyncio.sleep(0.7)
        gc.collect()
    return f"{last}. The file is still in use by ComfyUI: wait for the current generation to finish or restart ComfyUI, then delete it again."


@routes.post("/fooocus/lora/delete")
async def fooocus_lora_delete(request):
    """Body: {"name": <LoRA as ComfyUI lists it, e.g. "anima/style.safetensors">}"""
    try:
        rel = str((await request.json())["name"]).replace("\\", "/")
    except Exception:
        return web.json_response({"error": "expected JSON with name"}, status=400)
    if not rel.lower().endswith(_LORA_EXT):
        return web.json_response({"error": "only .safetensors files can be deleted"}, status=400)
    for root in _lora_roots():
        path = os.path.realpath(os.path.join(root, rel))
        try:
            inside = os.path.commonpath([root, path]) == root
        except ValueError:
            inside = False
        if inside and os.path.isfile(path):
            error = await _remove_file(path)
            if error:
                return web.json_response({"error": error}, status=500)
            return web.json_response({"deleted": rel})
    return web.json_response({"error": "file not found"}, status=404)


NODE_CLASS_MAPPINGS = {}
__all__ = ["NODE_CLASS_MAPPINGS"]
