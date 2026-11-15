# Fooocus-style UI for ComfyUI

A single-page web interface in the spirit of [Fooocus](https://github.com/lllyasviel/Fooocus) that runs on top of
ComfyUI. ComfyUI stays the engine; this page builds the workflow for you from a prompt and a few settings.

It adds **no nodes** and has **no dependencies**. After installing, open:

```
http://127.0.0.1:8188/fooocus
```

## What it does

- **Text to image** — prompt, negative prompt, performance presets (or a custom step count), the Fooocus aspect
  ratio list (or a custom size), image number, seed, live preview, Skip / Stop.
- **Styles** — the Fooocus style format with hover previews, search and "Random Style".
- **Models** — base model, VAE, five LoRA slots with weights, a Turbo LoRA for Extreme Speed.
- **Workflows** — built-in SDXL and Anima workflows, or import your own (API format).
- **Input Image**
  - *Upscale or Variation* — Vary (Subtle / Strong), Upscale 1.5x / 2x with an upscale model, Fast 2x.
  - *Inpaint or Outpaint* — paint a mask (brush, eraser, undo, zoom), three methods, outpaint in four directions,
    mask upload, invert, erode / dilate. The masked region is cropped, redrawn at about 1 MP and pasted back.
  - *Metadata* — reads parameters from PNG files (this UI, ComfyUI, Fooocus, A1111) and applies them.
- **History Log** — every image in the ComfyUI output folder with its parameters: search, pages of 100, delete.
- **Image viewer** — zoom, pan, previous / next, thumbnails.
- **Tag correctness check** — underlines prompt tags that are not booru tags.
- Works on phones (portrait layout).

## Install

**ComfyUI Manager** (once the node is published to the Comfy Registry): search for "Fooocus-style UI", install, restart ComfyUI.

**Manually:**

```
cd ComfyUI/custom_nodes
git clone https://github.com/Tand-Anthros/comfy-fooocus-ui.git
```

Restart ComfyUI and open `http://127.0.0.1:8188/fooocus`.

To use it from another device (a phone on the same network), start ComfyUI with `--listen` and open
`http://<address of the computer>:8188/fooocus`.

## Styles

Styles are read from `styles/*.json` in this folder (Fooocus format: a list of
`{"name", "prompt", "negative_prompt"}`, where `{prompt}` marks your text). Preview pictures are read from
`styles/samples/<style name>.jpg`.

The easiest way to fill the folder is to copy the contents of `sdxl_styles` from a Fooocus install, including
`samples`. Without the folder the page downloads the original Fooocus style files from GitHub on first use and
keeps them in the browser.

No style is selected by default: the prompt is sent as typed.

## LoRA folders

LoRAs are listed per workflow: the subfolder of `models/loras` whose name contains the workflow name
(for example `anima`, `lora_sdxl`) plus the files in the root of `models/loras`.

LoRA files with **`turbo`** in the name are treated as speed LoRAs: they are hidden from the regular slots and
offered as the *Turbo LoRA* at the bottom of the Models tab. It is chosen once per workflow and added automatically
when Performance is *Extreme Speed* (8 steps, CFG 1) or *Lightning* (4 steps, CFG 1).

## Inpaint shortcuts

They work while the pointer is over the picture, or after clicking inside the Inpaint tab.

| Key | Action |
|---|---|
| `E` / `Q` | Brush / Eraser |
| `Ctrl+Z` | Undo the last stroke |
| `Ctrl+Del` | Clear the mask |
| `Ctrl` + mouse wheel, `Ctrl` + `+` / `-` | Zoom (`Ctrl+0` resets) |
| hold `Space` and drag, or the *Move* tool | Move the zoomed picture |

## Image viewer

Click a picture in the gallery or in the History Log.

- Double click (double tap) zooms in; again zooms back out. `Ctrl` + mouse wheel and `Ctrl` + `+` / `-` zoom
  only the picture, not the page. On a phone, pinch.
- Drag to move around a zoomed picture.
- Click the left or right edge (or use the arrow keys, or swipe) for the previous / next picture.
- At 100 % a strip of thumbnails is shown at the bottom. `Esc` closes.

## History Log

- 100 images per page. Pages are numbered from the oldest: page 1 always holds the oldest hundred and never
  changes, the highest number is the newest and is the one that fills up. The links are listed newest first.
- The search box filters by any text in the parameters (prompt, LoRA, model, seed, date, file name); letter case
  does not matter.
- *Del* on a card shows a tick next to it; pressing the tick deletes the image file from the output folder for good.

## Tag correctness check

Advanced tab → *Tag correctness check*. Tags in the prompt boxes that are not in the booru tag list get a red
underline. It needs this extension installed (it does not work with the page opened as a file).

**Tag list.** Any `.csv` (first column) or `.txt` (one tag per line) file in the `tags` folder of the extension.
If the folder is empty, the *Download Danbooru tag list* button fetches `danbooru.csv` from the
[a1111-sd-webui-tagcomplete](https://github.com/DominikDoom/a1111-sd-webui-tagcomplete) project into it.

**Your dictionary.** *Add to dictionary* makes a word count as a valid tag, *Remove from dictionary* makes it
count as unknown. Both are saved on the server in `fooocus-ui-tags.json` in the ComfyUI folder — a small patch
file with an `add` and a `remove` list, created when missing with a few common quality and rating words
(`masterpiece`, `best quality`, …). You can edit it by hand.

**What is checked.** The prompt is split at commas and line breaks. Spaces and underscores are the same, letter
case is ignored.

**What is not checked** (so natural language can be mixed with tags, as Anima allows):

- A sentence: text that starts with a capital letter, up to the next period on the same line.
  `1girl, She is standing in a field, looking at the sky. long hair` — only `1girl` and `long hair` are checked.
  Without a period it is treated as a tag, so `Hatsune Miku` is still checked.
- A comment in parentheses after a tag: in `long hair (down to the floor)` only `long hair` is checked.
  Real tags with parentheses such as `artoria pendragon (fate)` are recognised as a whole.
- `<lora:...>`, `__wildcards__`, `BREAK` and plain numbers.

Emphasis is unwrapped first: `(tag:1.2)`, `((tag))` and `(tag a, tag b:1.1)` are checked as their tags.

## Your own workflows

Models tab → *Import workflow (API JSON)*. Export it from ComfyUI with *Workflow → Export (API)*. The workflow needs
a regular `KSampler`; prompt, size, seed, steps, CFG, sampler, model files and LoRAs are wired in automatically.

## Optional extensions

Everything above works on a plain ComfyUI. These are detected automatically and only add features:

| Extension | Adds |
|---|---|
| [ComfyUI Inpaint Nodes](https://github.com/Acly/comfyui-inpaint-nodes) + the Fooocus inpaint files in `models/inpaint` | Fooocus inpaint engine (SDXL only) |
| [segment anything](https://github.com/storyicon/comfyui_segment_anything) | Mask generation from a text prompt |
| [ComfyUI-WD14-Tagger](https://github.com/pythongosssss/ComfyUI-WD14-Tagger) | "Describe" (booru tags) for images without metadata |

## Using the page without installing

`fooocus-comfy.html` also works opened directly as a file. ComfyUI must then be started with
`--enable-cors-header`, and the address is set on the Advanced tab. In this mode styles come from GitHub, the
History Log only knows images made from that browser and since ComfyUI was last started (no delete), and the tag
check is not available.

## Limitations

- This is not Fooocus: its sampler internals (sharpness, adaptive CFG) and the "Fooocus V2" prompt expansion are
  not part of ComfyUI and are not reproduced.
- Without the optional inpaint extension, inpainting uses a noise mask with Differential Diffusion.

## Credits

The interface layout, wording and style format follow [Fooocus](https://github.com/lllyasviel/Fooocus) by
lllyasviel (GPL-3.0). No Fooocus source code is included. Built on [ComfyUI](https://github.com/comfyanonymous/ComfyUI).

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
