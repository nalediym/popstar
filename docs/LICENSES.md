# LICENSES — what this project uses, and what you may do with it

Audited 2026-11-02 by reading every model card / license page linked below. **Verified** = the license name was read
on the cited page that day. **UNVERIFIED** = could not be confirmed; treat as unknown, not as permissive. Quotes are
verbatim from the cited page. This is a good-faith audit, not legal advice.

## 1. Components

| Component (file / graph) | License (verbatim name) | Commercial use | Attribution required | Source | Status |
|---|---|---|---|---|---|
| **LTX-2.3 22B distilled fp8** `ltx-2.3-22b-distilled-fp8` (`pose-restyle`), `-dev-fp8` (`lipsync-ia2v`), spatial upscaler x2 1.1 | "LTX-2 Community License Agreement" (HF tag `ltx-2-community-license-agreement`), dated "January 5, 2026" | **Yes, royalty-free below the threshold**: "Entities with annual revenues of at least $10,000,000" "are required to obtain a paid commercial use license" (§2). Not in a product "that directly competes with Licensor's commercial products or services" (Att. A §20). Outputs: "Licensor claims no rights in the Output you generate using LTX-2." (§5) | Yes: "You must retain all copyright, patent, trademark, and attribution notices" (§3); derivatives ship under the same terms (§3(b)) | https://huggingface.co/Lightricks/LTX-2.3-fp8 · https://huggingface.co/Lightricks/LTX-2.3 · text: https://github.com/Lightricks/LTX-2/blob/main/LICENSE-2 (the HF `LICENSE` link 404s) | Verified |
| IC-LoRA `ltx-2.3-22b-ic-lora-union-control-ref0.5` | HF tag "ltx-2-community-license" (same agreement) | As above | As above | https://huggingface.co/Lightricks/LTX-2.3-22b-IC-LoRA-Union-Control | Verified |
| `ltx_2.3_22b_distilled_1.1_lora_dynamic_fro09_avg_rank_111_bf16` (`lipsync-ia2v` only) | LTX derivative → same agreement; host repo tag "ltx-2-community-license" | As above | As above | https://huggingface.co/Comfy-Org/ltx-2.3 (card does not list the file; third parties say Kijai extraction) | UNVERIFIED (provenance) |
| **Gemma 3 12B IT** text encoder `gemma_3_12B_it_fp4_mixed` | "Gemma Terms of Use" (HF tag `gemma`) | Yes, incl. "as a hosted service via API"; bound by the "Gemma Prohibited Use Policy at ai.google.dev/gemma/prohibited_use_policy" | Yes: distribution "must be accompanied by a "Notice" text file" reading "Gemma is provided under and subject to the Gemma Terms of Use" | https://ai.google.dev/gemma/terms · https://huggingface.co/google/gemma-3-12b-it · file: https://huggingface.co/Comfy-Org/ltx-2 | Verified |
| `gemma-3-12b-it-abliterated_lora_rank64_bf16` (community LoRA) | Gemma derivative → "Gemma Terms of Use" (base "License: gemma") | As Gemma. Abliteration removes refusals, not the Prohibited Use Policy | As Gemma | file: https://huggingface.co/Comfy-Org/ltx-2 (no card text) · probable base: https://huggingface.co/mlabonne/gemma-3-12b-it-abliterated | UNVERIFIED (who trained it) |
| **ACE-Step 1.5 XL Turbo** `acestep_v1.5_xl_turbo_bf16`, `ace_1.5_vae` (`song-acestep`) | "License: mit"; repo "MIT license". Card: "Generated music can be used for commercial purposes." | Yes | MIT notice | https://huggingface.co/ACE-Step/acestep-v15-xl-turbo · https://github.com/ACE-Step/ACE-Step-1.5 · repack (tag `apache-2.0`): https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files | Verified |
| ACE-Step text encoders `qwen_0.6b_ace15`, `qwen_4b_ace15` (Qwen3-based) | Shipped by ACE-Step (MIT) in the Comfy repack (tag `apache-2.0`); Qwen3 bases are Apache-2.0 | Yes | MIT/Apache notice | https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files | Verified (repo tags; no per-file statement) |
| **Qwen-Image 2.1 Turbo** `qwen_image_2.1_turbo_int8_convrot`, `qwen_image_2.1_vae_bf16` (`look-wardrobe`) | "Qwen RESEARCH LICENSE AGREEMENT" (HF tag `qwen-research`) | **NO**: "FOR NON-COMMERCIAL PURPOSES ONLY" (§2(a)); commercial use "without obtaining a separate commercial license from us" is prohibited (§2(b)) | "Built with Qwen" if used to build a distributed model (§4(b)) | https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo (LICENSE in tree) · repack: https://huggingface.co/Comfy-Org/Qwen-Image-2.1 | Verified |
| `qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot` prompt enhancer ("A fine-tuned Qwen3.5-VL 9B") | "License: qwen-research" | **NO** (same agreement) | As above | https://huggingface.co/Qwen/Qwen-Image-2.1-PE-I2I | Verified |
| `qwen3vl_8b_int8_convrot` text encoder (Qwen3-VL-8B-Instruct) | "License: apache-2.0" | Yes | Apache notice | https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct | Verified |
| **DWPose** `dw-ll_ucoco_384` (+ `_bs5.torchscript.pt`) | "Apache-2.0 license"; HF tags `apache-2.0` | Yes | Apache notice | https://github.com/IDEA-Research/DWPose · https://huggingface.co/yzd-v/DWPose · https://huggingface.co/hr16/DWPose-TorchScript-BatchSize5 | Verified |
| YOLOX-L detector `yolox_l.onnx` | "Apache-2.0 license" | Yes | Apache notice | https://github.com/Megvii-BaseDetection/YOLOX · file: https://huggingface.co/yzd-v/DWPose | Verified |
| comfyui_controlnet_aux (node pack) | "Apache-2.0 license" | Yes | — | https://github.com/Fannovel16/comfyui_controlnet_aux | Verified |
| **LivePortrait** core weights (via `Kijai/LivePortrait_safetensors`) | upstream "License: mit"; Kijai mirror has no license tag | Yes (MIT) | MIT notice | https://huggingface.co/KwaiVGI/LivePortrait · https://github.com/KwaiVGI/LivePortrait · https://huggingface.co/Kijai/LivePortrait_safetensors | Verified upstream; mirror UNVERIFIED |
| **ComfyUI-AdvancedLivePortrait** (`ExpressionEditor`, `look-expression`) | **No LICENSE file, no license badge** → all rights reserved by default | Unknown. Only run on Comfy Cloud; not vendored here | — | https://github.com/PowerHouseMan/ComfyUI-AdvancedLivePortrait | UNVERIFIED (none declared) |
| Face detector inside ExpressionEditor: ultralytics YOLOv8 + `face_yolov8n.pt` (nodes.py downloads from `Bingsu/adetailer`; **no InsightFace, no landmark.onnx**) | ultralytics "AGPL-3.0 license"; adetailer HF tag "apache-2.0" | Allowed, but AGPL is copyleft on the running stack (Comfy Cloud's problem, not this repo's) | — | https://github.com/ultralytics/ultralytics · https://huggingface.co/Bingsu/adetailer · https://github.com/PowerHouseMan/ComfyUI-AdvancedLivePortrait/blob/main/nodes.py | Verified |
| InsightFace (for the record — **not used** by the node above) | code MIT; models "available for non-commercial research purposes only." | Not applicable here | — | https://github.com/deepinsight/insightface · kijai's note "Insightface is strictly for NON-COMMERCIAL use.": https://github.com/kijai/ComfyUI-LivePortraitKJ | Verified; the LivePortrait README itself has no such note today |
| **Wan2.1 I2V 14B 480P** `wan2.1_i2v_480p_14B_fp16`, VAE, `clip_vision_h` (`lipsync-infinitetalk`, test only) | "License: apache-2.0"; "We claim no rights over the your generate contents" | Yes | Apache notice | https://huggingface.co/Wan-AI/Wan2.1-I2V-14B-480P · repack: https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged | Verified |
| InfiniteTalk `Wan2_1-InfiniTetalk-Single_fp16` | "The models in this repository are licensed under the Apache 2.0 License." | Yes | Apache notice | https://huggingface.co/MeiGen-AI/InfiniteTalk · https://github.com/MeiGen-AI/InfiniteTalk · fp16 mirror (no tag): https://huggingface.co/Kijai/WanVideo_comfy | Verified upstream; mirror UNVERIFIED |
| chinese-wav2vec2-base | "License: mit" | Yes | MIT notice | https://huggingface.co/TencentGameMate/chinese-wav2vec2-base | Verified |
| lightx2v distill LoRA `lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16` | upstream "License: apache-2.0" | Yes | Apache notice | https://huggingface.co/lightx2v/Wan2.1-I2V-14B-480P-StepDistill-CfgDistill-Lightx2v · rank64 file: https://huggingface.co/Kijai/WanVideo_comfy (no tag) | Verified upstream; exact file UNVERIFIED |
| umt5-xxl encoder `umt5-xxl-enc-bf16` | google/umt5-xxl "License: apache-2.0" | Yes | Apache notice | https://huggingface.co/google/umt5-xxl | Verified (bf16 conversion from Kijai: no tag) |
| **MoGe-2** `moge_2_vitl_normal_fp16` (in the template, **not in our graph**) | code "MIT license"; weights "License: mit" | Yes | MIT notice | https://github.com/microsoft/MoGe · https://huggingface.co/Ruicheng/moge-2-vitl-normal | Verified |
| **Whisper small** via mlx-whisper (`mlx-community/whisper-small-mlx`) | "Whisper's code and model weights are released under the MIT License."; mlx-whisper "MIT"; the MLX conversion card has **no license tag** | Yes | MIT notice | https://github.com/openai/whisper · https://pypi.org/project/mlx-whisper/ · https://huggingface.co/mlx-community/whisper-small-mlx | Verified upstream; conversion UNVERIFIED |
| **beat_this** (CPJKU) | "The code and the published model weights are released under the MIT license." | Yes | MIT notice | https://github.com/CPJKU/beat_this | Verified |
| **MediaPipe** Tasks + `pose_landmarker_lite` (recorder; Python) | repo "Apache-2.0 license"; model card "LICENSED UNDER Apache License, Version 2.0" | Yes | Apache notice | https://github.com/google-ai-edge/mediapipe · https://storage.googleapis.com/mediapipe-assets/Model%20Card%20BlazePose%20GHUM%203D.pdf | Verified (Face Landmarker card not checked) |
| **ComfyUI** | "GPL-3.0 license" | Yes — we ship JSON graphs, not ComfyUI code; GPL does not reach outputs | — | https://github.com/comfyanonymous/ComfyUI | Verified |
| **Comfy Cloud** Terms of Service ("Effective Date: May 13, 2026") | §2 "Customer Data": "Customer retains all right, title, and interest in and to" Input and Output; "Comfy will not use Input or Output to train generative AI or diffusion models." | Outputs are ours | — | https://comfy.org/terms-of-service | Verified |
| ComfyUI-LTXVideo (Lightricks nodes) / ComfyUI-WanVideoWrapper (Kijai) | "LTX-2 Community License Agreement" / "Apache-2.0 license" | Yes (LTX terms apply to the node pack too) | LTX notices | https://github.com/Lightricks/ComfyUI-LTXVideo · https://github.com/kijai/ComfyUI-WanVideoWrapper | Verified |
| **Blender** (previs) | "GNU General Public License"; "What you create with Blender is your sole property." | Yes | — | https://www.blender.org/about/license/ | Verified |
| **ffmpeg 9** (stitch) | "GNU Lesser General Public License (LGPL) version 2.1 or later"; with GPL parts "the GPL applies to all of FFmpeg" | Yes; we call the binary, outputs unaffected (installed build's flags not checked) | — | https://ffmpeg.org/legal.html | Verified |
| **Bun** / **uv** | "Bun itself is MIT-licensed." / "Apache License, Version 2.0" or "MIT license" "at your option" | Yes | — | https://github.com/oven-sh/bun/blob/main/LICENSE.md · https://github.com/astral-sh/uv | Verified |
| **Light leaks** `LightLeakSoftFocus`, `LightLeakHardEdge`, `LightLeak`, `LightLeakFastMotion` | `licenseurl` http://creativecommons.org/licenses/by/3.0/ = "Attribution 3.0 Unported"; creator "Jeffrey Beach (Beachfront Productions)" | Yes: "for any purpose, even commercially" | **Yes**: "must give appropriate credit", link the license, say if changed | https://archive.org/details/LightLeakSoftFocus · …/LightLeakHardEdge · …/LightLeak · …/LightLeakFastMotion (metadata at https://archive.org/metadata/ID) | Verified (uploader-asserted) |
| **Film leader** `FilmLeader` | `licenseurl` http://creativecommons.org/licenses/publicdomain/ = CC "Public Domain Certification 1.0 United States" (retired tool); creator "The Chosen One" | Yes | No (courtesy credit) | https://archive.org/details/FilmLeader | Verified (uploader-asserted; underlying footage provenance unknown) |

Which LTX license? **"LTX-2 Community License Agreement"** governs LTX-2.x including 2.3 (model, IC-LoRA, fp8 repo,
node pack). The older **"LTXV Open Weights License 2025-05-05"** names only "LTX Video-13b-0.9.7-dev-fp8"
(https://scancode-licensedb.aboutcode.org/ltxv-owl-2025-05-05.html); same $10M idea, not this model.

## 2. What this means for this project

- **Open Weights bonus: eligible.** Every model above is downloadable weights; no partner nodes, no closed APIs
  (`hackathon-context.json`: "Open-weights models without partner nodes or closed model APIs."). But "open weights"
  is not "open-source license": LTX (Community License), Gemma (Terms of Use) and Qwen-Image 2.1 (Research License)
  are not OSI licenses. Say that plainly in the README; the hackathon's "open-source license" requirement is about
  *this repo's* license, which we control (§4).
- **The one commercial blocker is Qwen-Image 2.1 Turbo, not LivePortrait.** `look-wardrobe` (Reference Master
  wardrobe / set / light edits) and its PE-I2I prompt enhancer are "FOR NON-COMMERCIAL PURPOSES ONLY". Every other
  model in the critical path (LTX, Gemma, ACE-Step, DWPose, YOLOX, LivePortrait, Wan, InfiniteTalk, MediaPipe,
  Whisper, beat_this) permits commercial use with notices.
  **Drop-in:** `Qwen/Qwen-Image-Edit-2509` — card: "Qwen-Image is licensed under Apache 2.0."
  (https://huggingface.co/Qwen/Qwen-Image-Edit-2509). Same prompt-driven edit, same identity-lock prompts; re-pin
  `look-wardrobe` to ComfyUI's Qwen-Image-Edit template. It also does expression edits by prompt, which would let us
  retire `ExpressionEditor` and its unlicensed node pack in one move. Until swapped, the demo is research/evaluation
  use: fine for the hackathon, **do not sell or license the video**.
- **LivePortrait / InsightFace, corrected.** `ExpressionEditor` (ComfyUI-AdvancedLivePortrait) loads MIT LivePortrait
  weights and detects faces with ultralytics YOLOv8 (`face_yolov8n.pt`), **not InsightFace** — so the InsightFace
  non-commercial clause does not apply. The real issues: the node pack declares **no license at all**, and ultralytics
  is AGPL-3.0 (copyleft, not a ban; it runs on Comfy Cloud, nothing of it lives in this repo). Swap per the bullet
  above, or keep it and mark `look-expression` "demo only".
- **LTX:** free for an individual or any entity under $10M revenue; a paid license is needed at or above that, or in a
  product competing with Lightricks. Keep its notices; outputs are ours (§5).
- **Gemma:** ship the Notice line below; the abliterated LoRA is still bound by the Prohibited Use Policy.
- **Wan2.1 / InfiniteTalk lip-sync path** (if kept): entirely Apache-2.0 / MIT — commercially clean.
- **Outputs:** Comfy Cloud, Blender, LTX and Wan all leave output rights with us; ACE-Step music may be sold.
  Her face and voice are hers — keep the README consent line (architecture R5).
- **Repo hygiene:** `PLAN.md` "Done" #3 claims a LICENSE is committed; **there is no LICENSE file in the repo yet**.

## 3. Credits block — paste into README.md

```markdown
## Credits
Light-leak footage by Jeffrey Beach (Beachfront Productions) via the Internet Archive — "Light Leak Soft Focus",
"Light Leak Hard Edge", "Light Leak Special Effect", "Light Leak Fast Motion" — CC BY 3.0
(https://creativecommons.org/licenses/by/3.0/); cropped, retimed and blended.
https://archive.org/details/LightLeakSoftFocus · https://archive.org/details/LightLeakHardEdge ·
https://archive.org/details/LightLeak · https://archive.org/details/LightLeakFastMotion
"Film Leader" countdown by The Chosen One via the Internet Archive, public domain — https://archive.org/details/FilmLeader

LTX-2.3 © Lightricks Ltd., used under the LTX-2 Community License Agreement
(https://github.com/Lightricks/LTX-2/blob/main/LICENSE-2). Gemma is provided under and subject to the Gemma Terms
of Use found at ai.google.dev/gemma/terms. Qwen-Image 2.1 Turbo © Alibaba Cloud, Qwen Research License Agreement
(non-commercial). ACE-Step 1.5 (MIT) · DWPose (Apache-2.0) · YOLOX (Apache-2.0) · LivePortrait (MIT) ·
Wan2.1 and InfiniteTalk (Apache-2.0) · chinese-wav2vec2-base (MIT) · Whisper (MIT) · beat_this (MIT) ·
MediaPipe (Apache-2.0) · MoGe-2 (MIT). Built with ComfyUI (GPL-3.0) on Comfy Cloud; stitched with FFmpeg (LGPL)
and previs in Blender (GPL). Model weights are not redistributed in this repo.
```

## 4. Repo license: Apache-2.0

Pick **Apache-2.0** over MIT: its NOTICE file is the natural home for the Gemma Notice line and LTX's
notice-retention duty, it adds an explicit patent grant, and the permissive dependencies here (DWPose, YOLOX, Wan,
MediaPipe, uv) already use it. Add `LICENSE` (Apache-2.0, © 2026 Naledi) and `NOTICE` (the Credits block) before
flipping the repo public.

## Apache-licensed wardrobe graph (swap test)

Tested 2026-11-02: can the §2 drop-in replace `look-wardrobe` (Qwen-Image 2.1 Turbo, research license) without losing
her? One render, judged against the Turbo render made earlier tonight with the same prompt and seed.

**Template and weights.** Comfy Cloud template `image_qwen_image_edit_2511_int8` (the sibling `image_qwen_image_edit_2509`
is the same license). Every file in the executed graph is Apache-2.0, read on the cited pages 2026-11-02:

| File (as loaded on Cloud) | License (verbatim) | Source |
|---|---|---|
| `qwen_image_edit_2511_int8_convrot` (UNETLoader) | "License: apache-2.0"; card: "Qwen-Image is licensed under Apache 2.0." | https://huggingface.co/Qwen/Qwen-Image-Edit-2511 · int8 repack (tag "License: apache-2.0"): https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI |
| `Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16` (LoRA, turbo mode) | "License: apache-2.0"; base model listed as Qwen/Qwen-Image-Edit-2511 | https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning |
| `qwen_2.5_vl_7b_fp8_scaled` (text encoder) | "License: apache-2.0" | https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct (file hosted in Comfy-Org/HunyuanVideo_1.5_repackaged) |
| `qwen_image_vae` | Qwen-Image VAE, Apache-2.0 (same card sentence) | https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI |
| for the record: `Qwen/Qwen-Image-Edit-2509` | "License: apache-2.0"; card: "Qwen-Image is licensed under Apache 2.0." | https://huggingface.co/Qwen/Qwen-Image-Edit-2509 |

**Run.** Job `8cb83779-5725-49ee-a832-4f93ab108a38` (Cloud workflow `ad18abd3-bdee-4f80-8fae-d15d4f845b9d`): her frame
`660bf887….png`, the `look-wardrobe` wardrobe prompt verbatim, seed 11, `enable_turbo_mode=true` (4 steps, CFG 1,
Lightning LoRA, the analog of the 8-step Turbo path). Done inside the ~25 s one-shot window. Output 1392x752: the
template's `FluxKontextImageScale` snaps a 1280x720 input to the nearest Kontext size, so it is not the 1280x704 the
Turbo graph gives. Files: `build/license-swap/apache.png`, `turbo.png`, `compare.png` (face crops, Apache left),
`compare3.png` (source | Apache | Turbo).

**Verdict: FAIL on the identity gate.** Skin tone, wardrobe and set pass; the face, hair and framing do not.

- Face: reshaped into a different woman. Slimmer oval face, sculpted cheekbones, narrower nose, thinner lips, mouth
  relaxed and closed. The source and the Turbo render both keep her round face, full cheeks, broad nose and pursed lips.
- Hair: slicked down to a near buzz cut with a copper tint. Turbo keeps the copper-red pulled-back hair with its volume.
- Pose and framing: not kept. The shot is recomposed wider (her face about two-thirds the size), a large out-of-focus
  forearm enters at the bottom, and "a large soft warm key light from camera left" was rendered as a visible fresnel lamp
  in frame. Turbo keeps the close selfie framing.
- Skin tone: kept, not lightened. Mean RGB of 40x40 cheek patches (both renders are under dim amber stage light, the
  source under a bright atrium): viewer-right cheek source 102/61/42, Apache 77/34/16, Turbo 68/29/6; key-lit
  viewer-left cheek source 157/104/77, Apache 178/117/63, Turbo 138/69/27. Apache keeps more blue in the skin than
  Turbo's crushed orange; both read as rich deep brown.
- Wardrobe and set: applied. Burnt-orange satin slip dress with thin straps, small gold hoops, earbuds kept; fairy-light
  strings, Tuscan brown velvet curtains, candles and haze. If anything too literal (the lamp).
- Per-pixel quality: sharper and more cinematic than Turbo (shallow depth of field, higher resolution), but fidelity to
  the source is far worse. Quality is not equal, so it cannot be the Reference Master default on this evidence.

**Recommendation.** Keep `look-wardrobe` (Qwen-Image 2.1 Turbo) for the hackathon demo, which stays research /
non-commercial use (§2). Do not promote `look-wardrobe-apache`. The graph is pinned anyway
(`workflows/look-wardrobe-apache.api.json`, slots in `slots.json`, every file Apache-2.0) so the next attempt is a
one-liner. In order of promise, each one render:

1. `bun tools/comfy_compile.ts look-wardrobe-apache --set turbo=false` (40 steps, CFG 4, no Lightning LoRA: the
   template's own default, untested here). The 4-step distillation is the prime suspect for the identity drift, and at
   CFG 4 a negative prompt ("different face, slim face, buzz cut, wide shot, lamp in frame") starts to work.
2. Same, with a harder lock in the prompt: "Do not change her face shape, nose, lips, hair or the close selfie framing."
3. If both drift, `image_qwen_image_edit_2509` (same license), or a structural edit that feeds the source frame as a
   second reference image (`image2`) so the model has the face twice.

If a pass comes, re-pin from that job, make it the default in the `reference-master` skill, and move the Qwen-Image 2.1
Turbo row in §1 to "not used".
