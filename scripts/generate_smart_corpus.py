#!/usr/bin/env python3
"""Deterministic synthetic SMART video corpus generator.

Generates reproducible FFV1 SDR base source video clips and corpus manifests
for SMART product evaluation. Base recipe groups across development, calibration,
acceptance, and long splits are strictly disjoint, holding out complex motion
trajectories, multi-octave texture variants, and compound event combinations for
acceptance.

Pure standard-library generator using explicit FFmpeg/FFprobe argv without shell.
No imports from SMART sampling, risk, or evaluation algorithm modules.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

SPLIT_CHOICES = ("development", "calibration", "acceptance", "long", "all")
SCENE_FAMILIES = (
    "moving_textures",
    "lines_and_text",
    "dark_gradients",
    "film_noise",
    "transitions",
    "short_difficult_events",
)


@dataclasses.dataclass(frozen=True, slots=True)
class CaseRecipe:
    id: str
    split: str
    group: str
    scene_family: str
    seed: int
    duration_sec: float
    fps: int
    width: int
    height: int
    bit_depth: int  # 8 or 10
    event_times: tuple[dict[str, Any], ...]
    recipe_type: str
    recipe_params: dict[str, Any]

    @property
    def pix_fmt(self) -> str:
        return "yuv420p10le" if self.bit_depth == 10 else "yuv420p"


# (id, split, group, family, base_seed, base_dur, fps, w, h, bit_depth, recipe_type, base_params, base_event)
_RAW_SPECS: tuple[tuple[str, str, str, str, int, float, int, int, int, int, str, dict[str, Any], tuple[float, float, str] | None], ...] = (
    # --------------------------------------------------------------------------
    # 1. DEVELOPMENT SPLIT (12 cases: 2 per scene family)
    # --------------------------------------------------------------------------
    ("smart-dev-01-tex-scroll", "development", "dev_tex_scroll", "moving_textures", 1001, 45.0, 30, 1920, 1080, 8,
     "moving_texture_scroll", {"h_speed": 0.004, "v_speed": 0.002, "base": "testsrc2"}, None),
    ("smart-dev-02-tex-plasma", "development", "dev_tex_plasma", "moving_textures", 1002, 60.0, 24, 1280, 720, 10,
     "moving_texture_plasma", {"speed": 2.5}, None),
    ("smart-dev-03-lines-ticker", "development", "dev_lines_ticker", "lines_and_text", 1003, 75.0, 60, 1920, 1080, 8,
     "lines_ticker", {"ticker_speed": 130, "text": "DEV RUN // BENCHMARK STREAM 01 // STABLE CADENCE"}, None),
    ("smart-dev-04-lines-grid", "development", "dev_lines_grid", "lines_and_text", 1004, 50.0, 30, 2560, 1440, 10,
     "lines_grid_sweep", {"grid_size": 80, "sweep_speed": 90}, None),
    ("smart-dev-05-dark-linear", "development", "dev_dark_linear", "dark_gradients", 1005, 90.0, 24, 1920, 1080, 10,
     "dark_gradient_linear", {"c0_luma": 16, "c1_luma": 96}, None),
    ("smart-dev-06-dark-radial", "development", "dev_dark_radial", "dark_gradients", 1006, 65.0, 30, 1280, 720, 8,
     "dark_gradient_radial", {"c0_luma": 110, "c1_luma": 12}, None),
    ("smart-dev-07-noise-fine", "development", "dev_noise_fine", "film_noise", 1007, 80.0, 24, 1920, 1080, 8,
     "film_noise_fine", {"strength": 16}, None),
    ("smart-dev-08-noise-coarse", "development", "dev_noise_coarse", "film_noise", 1008, 100.0, 60, 1280, 720, 10,
     "film_noise_coarse", {"luma_strength": 32, "chroma_strength": 6}, None),
    ("smart-dev-09-trans-dissolve", "development", "dev_trans_dissolve", "transitions", 1009, 55.0, 30, 1920, 1080, 8,
     "transition_xfade", {"transition": "fade"}, (27.0, 2.0, "cross_dissolve")),
    ("smart-dev-10-trans-wipe", "development", "dev_trans_wipe", "transitions", 1010, 110.0, 24, 1920, 1080, 10,
     "transition_xfade", {"transition": "wipeleft"}, (54.0, 2.0, "horizontal_wipe")),
    ("smart-dev-11-event-flash", "development", "dev_event_flash", "short_difficult_events", 1011, 70.0, 60, 1920, 1080, 8,
     "event_flash", {"intensity": 0.75}, (15.0, 1.5, "luma_flash_burst")),
    ("smart-dev-12-event-glitch", "development", "dev_event_glitch", "short_difficult_events", 1012, 120.0, 30, 3840, 2160, 10,
     "event_glitch", {"noise_strength": 90}, (45.0, 1.0, "block_entropy_glitch")),

    # --------------------------------------------------------------------------
    # 2. CALIBRATION SPLIT (12 cases: disjoint groups)
    # --------------------------------------------------------------------------
    ("smart-cal-01-tex-diagwave", "calibration", "cal_tex_diagwave", "moving_textures", 2001, 50.0, 24, 1920, 1080, 8,
     "moving_texture_scroll", {"h_speed": 0.003, "v_speed": -0.003, "base": "testsrc"}, None),
    ("smart-cal-02-tex-mandelbrot", "calibration", "cal_tex_mandelbrot", "moving_textures", 2002, 70.0, 30, 1280, 720, 10,
     "moving_texture_mandelbrot", {"maxiter": 120}, None),
    ("smart-cal-03-lines-vertical", "calibration", "cal_lines_vertical", "lines_and_text", 2003, 85.0, 60, 1920, 1080, 8,
     "lines_vertical_crawl", {"crawl_speed": 70, "text": "CALIBRATION LOG // VECTOR STREAM // TELEMETRY FRAME"}, None),
    ("smart-cal-04-lines-crosshairs", "calibration", "cal_lines_crosshairs", "lines_and_text", 2004, 60.0, 30, 2560, 1440, 10,
     "lines_crosshairs", {"radius": 150, "rot_speed": 1.8}, None),
    ("smart-cal-05-dark-corner", "calibration", "cal_dark_corner", "dark_gradients", 2005, 95.0, 24, 1920, 1080, 10,
     "dark_gradient_linear", {"c0_luma": 10, "c1_luma": 120, "invert": True}, None),
    ("smart-cal-06-dark-conic", "calibration", "cal_dark_conic", "dark_gradients", 2006, 45.0, 30, 1280, 720, 8,
     "dark_gradient_conic", {"c0": "0x020202", "c1": "0x161616", "speed": 0.05}, None),
    ("smart-cal-07-noise-speckle", "calibration", "cal_noise_speckle", "film_noise", 2007, 80.0, 60, 1920, 1080, 10,
     "film_noise_speckle", {"strength": 24}, None),
    ("smart-cal-08-noise-chromaflicker", "calibration", "cal_noise_chromaflicker", "film_noise", 2008, 105.0, 24, 1920, 1080, 8,
     "film_noise_chroma", {"luma_strength": 12, "chroma_strength": 38}, None),
    ("smart-cal-09-trans-dipblack", "calibration", "cal_trans_dipblack", "transitions", 2009, 65.0, 30, 1920, 1080, 8,
     "transition_xfade", {"transition": "fadeblack"}, (31.5, 2.5, "dip_to_black")),
    ("smart-cal-10-trans-boxexpand", "calibration", "cal_trans_boxexpand", "transitions", 2010, 115.0, 24, 1280, 720, 10,
     "transition_xfade", {"transition": "circleopen"}, (56.5, 2.0, "circle_open_iris")),
    ("smart-cal-11-event-cornerparticle", "calibration", "cal_event_cornerparticle", "short_difficult_events", 2011, 75.0, 60, 1920, 1080, 8,
     "event_particle", {"speed": 400}, (18.0, 1.2, "corner_velocity_particle")),
    ("smart-cal-12-event-pulseanomaly", "calibration", "cal_event_pulseanomaly", "short_difficult_events", 2012, 120.0, 30, 3840, 2160, 10,
     "event_pulse", {"contrast": 2.2}, (28.0, 2.0, "high_freq_pulse_anomaly")),

    # --------------------------------------------------------------------------
    # 3. ACCEPTANCE SPLIT (12 cases: held-out trajectories, textures, combinations)
    # --------------------------------------------------------------------------
    ("smart-acc-01-tex-lissajous", "acceptance", "acc_tex_lissajous", "moving_textures", 3001, 60.0, 60, 1920, 1080, 10,
     "heldout_tex_lissajous", {"freq_x": 3, "freq_y": 4, "phase": 0.5}, None),
    ("smart-acc-02-tex-chaoticshear", "acceptance", "acc_tex_chaoticshear", "moving_textures", 3002, 90.0, 30, 1920, 1080, 8,
     "heldout_tex_chaoticshear", {"acceleration": 1.4}, None),
    ("smart-acc-03-lines-telemetry", "acceptance", "acc_lines_telemetry", "lines_and_text", 3003, 80.0, 60, 1920, 1080, 10,
     "heldout_lines_telemetry", {"text": "ACCEPTANCE HUD // ALT 18400 // VEL 482 // HDG 284"}, None),
    ("smart-acc-04-lines-densegrid", "acceptance", "acc_lines_densegrid", "lines_and_text", 3004, 55.0, 24, 2560, 1440, 8,
     "heldout_lines_densegrid", {"cell_size": 24}, None),
    ("smart-acc-05-dark-dithersteps", "acceptance", "acc_dark_dithersteps", "dark_gradients", 3005, 100.0, 24, 1920, 1080, 10,
     "heldout_dark_dithersteps", {"steps": 16}, None),
    ("smart-acc-06-dark-shadowcreep", "acceptance", "acc_dark_shadowcreep", "dark_gradients", 3006, 70.0, 30, 1280, 720, 8,
     "heldout_dark_shadowcreep", {"speed": 0.06}, None),
    ("smart-acc-07-noise-burstysalt", "acceptance", "acc_noise_burstysalt", "film_noise", 3007, 85.0, 30, 1920, 1080, 10,
     "heldout_noise_burstysalt", {"burst_period": 5.0, "salt_strength": 40}, None),
    ("smart-acc-08-noise-analogstreak", "acceptance", "acc_noise_analogstreak", "film_noise", 3008, 110.0, 24, 1920, 1080, 8,
     "heldout_noise_analogstreak", {"streak_rate": 12}, None),
    ("smart-acc-09-trans-strobewhip", "acceptance", "acc_trans_strobewhip", "transitions", 3009, 45.0, 60, 1920, 1080, 10,
     "heldout_trans_strobe", {"strobe_freq": 15}, (22.0, 1.5, "strobe_whip_phase_shift")),
    ("smart-acc-10-trans-splitpush", "acceptance", "acc_trans_splitpush", "transitions", 3010, 95.0, 30, 1920, 1080, 8,
     "heldout_trans_splitpush", {}, (47.0, 2.0, "split_push_desaturate")),
    ("smart-acc-11-event-microtransient", "acceptance", "acc_event_microtransient", "short_difficult_events", 3011, 65.0, 60, 1920, 1080, 10,
     "heldout_event_microtransient", {"occlusion_speed": 400}, (20.0, 0.3, "micro_transient_occlusions")),
    ("smart-acc-12-event-compoundburst", "acceptance", "acc_event_compoundburst", "short_difficult_events", 3012, 120.0, 24, 3840, 2160, 10,
     "heldout_event_compoundburst", {"luma_boost": 0.65, "noise_boost": 80}, (35.0, 1.8, "compound_burst_flicker_displacement")),

    # --------------------------------------------------------------------------
    # 4. LONG SPLIT (6 rare-event cases, 600.0s each)
    # --------------------------------------------------------------------------
    ("smart-long-01-rare-statictick", "long", "long_rare_statictick", "short_difficult_events", 4001, 600.0, 30, 1920, 1080, 8,
     "long_rare_static_needle", {}, (184.0, 1.5, "rare_static_needle_tick")),
    ("smart-long-02-rare-subtledrift", "long", "long_rare_subtledrift", "dark_gradients", 4002, 600.0, 24, 1920, 1080, 10,
     "long_rare_dark_flare", {}, (421.0, 2.0, "rare_dark_motion_flare")),
    ("smart-long-03-rare-burstluma", "long", "long_rare_burstluma", "film_noise", 4003, 600.0, 30, 1280, 720, 8,
     "long_rare_noise_burst", {}, (95.0, 1.8, "rare_noise_burst_anomaly")),
    ("smart-long-04-rare-gradientband", "long", "long_rare_gradientband", "dark_gradients", 4004, 600.0, 24, 2560, 1440, 10,
     "long_rare_gradient_step", {"step_levels": 4}, (512.0, 2.5, "rare_gradient_banding_step")),
    ("smart-long-05-rare-microtexture", "long", "long_rare_microtexture", "moving_textures", 4005, 600.0, 60, 1920, 1080, 8,
     "long_rare_texture_surge", {}, (260.0, 1.2, "rare_texture_energy_surge")),
    ("smart-long-06-rare-glitchburst", "long", "long_rare_glitchburst", "lines_and_text", 4006, 600.0, 30, 1920, 1080, 10,
     "long_rare_vector_glitch", {}, (370.0, 1.0, "rare_vector_glitch_burst")),
)


def _build_recipes() -> tuple[CaseRecipe, ...]:
    """Build canonical 42 reproducible recipe cases with meaningful seeded jitter."""
    recipes: list[CaseRecipe] = []

    for item in _RAW_SPECS:
        cid, split, group, family, base_seed, base_dur, fps, w, h, bit_depth, r_type, base_params, ev_spec = item
        params = dict(base_params)

        # Apply deterministic seeded jitter to motion speeds and pattern params
        if "h_speed" in params:
            jitter_h = 1.0 + ((base_seed % 11) - 5) * 0.03
            params["h_speed"] = round(params["h_speed"] * jitter_h, 6)
        if "v_speed" in params:
            jitter_v = 1.0 + (((base_seed >> 3) % 11) - 5) * 0.03
            params["v_speed"] = round(params["v_speed"] * jitter_v, 6)
        if "ticker_speed" in params:
            params["ticker_speed"] = int(params["ticker_speed"] + (base_seed % 15) - 7)

        # Apply deterministic seeded jitter to event placement
        event_times: tuple[dict[str, Any], ...] = ()
        if ev_spec is not None:
            base_s, base_len, ev_label = ev_spec
            jitter_start = ((base_seed * 17) % 19 - 9) * 0.1
            jitter_len = ((base_seed * 31) % 7 - 3) * 0.05
            actual_start = max(1.0, round(base_s + jitter_start, 3))
            actual_len = max(0.2, round(base_len + jitter_len, 3))
            actual_end = round(actual_start + actual_len, 3)
            event_times = ({"start_sec": actual_start, "end_sec": actual_end, "label": ev_label},)

        recipes.append(
            CaseRecipe(
                id=cid,
                split=split,
                group=group,
                scene_family=family,
                seed=base_seed,
                duration_sec=base_dur,
                fps=fps,
                width=w,
                height=h,
                bit_depth=bit_depth,
                event_times=event_times,
                recipe_type=r_type,
                recipe_params=params,
            )
        )

    return tuple(recipes)


ALL_RECIPES = _build_recipes()


def detect_drawtext(ffmpeg: Path) -> bool:
    """Return whether the specified ffmpeg binary has the drawtext filter."""
    try:
        proc = subprocess.run(
            [str(ffmpeg), "-filters"],
            capture_output=True,
            text=True,
            check=False,
        )
        return "drawtext" in proc.stdout
    except OSError:
        return False


def resolve_font_file(font_file: Path | None = None) -> Path:
    """Validate and resolve explicit font file path. OS font fallbacks are not permitted."""
    if font_file is None:
        raise ValueError("An explicit font file must be provided via --font-file")
    resolved = Path(font_file).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Specified font file does not exist: {resolved}")
    return resolved


def rasterize_text_overlay_pillow(
    font_file: Path,
    text: str,
    width: int,
    height: int,
    output_png: Path,
    font_size: int = 24,
    box_color: tuple[int, int, int, int] = (0, 0, 40, 180),
    text_color: tuple[int, int, int, int] = (255, 255, 255, 255),
) -> None:
    """Rasterize text to a transparent PNG using Pillow when drawtext is unavailable."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError(
            "Pillow is required for font rasterization fallback, but is not installed."
        ) from exc

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(str(font_file), font_size)
    except OSError as exc:
        raise RuntimeError(f"Failed to load font {font_file}: {exc}") from exc

    bar_height = font_size + 16
    draw.rectangle([0, height - bar_height, width, height], fill=box_color)
    draw.text((20, height - bar_height + 8), text, font=font, fill=text_color)
    img.save(output_png, format="PNG")


def _scale_event_times(
    events: tuple[dict[str, Any], ...],
    original_duration: float,
    new_duration: float,
) -> tuple[dict[str, Any], ...]:
    """Scale event times proportionally when duration is overridden for smoke tests."""
    if not events or original_duration <= 0 or new_duration <= 0:
        return ()
    ratio = new_duration / original_duration
    scaled: list[dict[str, Any]] = []
    for ev in events:
        s = round(ev["start_sec"] * ratio, 3)
        e = round(ev["end_sec"] * ratio, 3)
        if e <= s:
            e = round(min(new_duration, s + 0.1), 3)
        scaled.append({"start_sec": s, "end_sec": e, "label": ev.get("label", "")})
    return tuple(scaled)


def build_ffmpeg_filter_args(
    recipe: CaseRecipe,
    font_file: Path,
    has_drawtext: bool,
    work_dir: Path,
    smoke_duration: float | None = None,
    smoke_width: int | None = None,
    smoke_height: int | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Build the exact argv inputs and filter graph for the given recipe.

    Returns (ffmpeg_inputs_and_filters, font_meta).
    """
    duration = smoke_duration if smoke_duration is not None else recipe.duration_sec
    width = smoke_width if smoke_width is not None else recipe.width
    height = smoke_height if smoke_height is not None else recipe.height
    fps = recipe.fps
    pix_fmt = recipe.pix_fmt
    seed = recipe.seed

    font_meta: dict[str, Any] = {
        "font_file": str(font_file),
        "font_name": font_file.stem,
        "method": "drawtext" if has_drawtext else "pillow_overlay",
    }

    events = recipe.event_times
    if smoke_duration is not None and recipe.event_times:
        events = _scale_event_times(recipe.event_times, recipe.duration_sec, smoke_duration)
    ev_start = events[0]["start_sec"] if events else 1.0
    ev_end = events[0]["end_sec"] if events else 2.0

    t = recipe.recipe_type

    # 1. Moving textures
    if t == "moving_texture_scroll":
        base = recipe.recipe_params.get("base", "testsrc2")
        h_s = recipe.recipe_params.get("h_speed", 0.004)
        v_s = recipe.recipe_params.get("v_speed", 0.002)
        vf = f"scroll=horizontal={h_s}:vertical={v_s},format={pix_fmt}"
        return ["-f", "lavfi", "-i", f"{base}=s={width}x{height}:r={fps}", "-vf", vf, "-t", str(duration)], font_meta

    if t == "moving_texture_plasma":
        sub_w, sub_h = max(160, width // 4), max(120, height // 4)
        vf = (
            f"geq=r='128+120*sin(hypot(X-W/2,Y-H/2)/12-T*3)':"
            f"g='128+120*sin(hypot(X-W/2,Y-H/2)/16-T*2)':"
            f"b='128+120*cos(hypot(X-W/2,Y-H/2)/20-T*2.5)',"
            f"scale={width}:{height}:flags=bicubic,format={pix_fmt}"
        )
        return ["-f", "lavfi", "-i", f"nullsrc=s={sub_w}x{sub_h}:r={fps}", "-vf", vf, "-t", str(duration)], font_meta

    if t == "moving_texture_mandelbrot":
        vf = f"format={pix_fmt}"
        return ["-f", "lavfi", "-i", f"mandelbrot=s={width}x{height}:r={fps}:maxiter=100", "-vf", vf, "-t", str(duration)], font_meta

    if t == "heldout_tex_lissajous":
        # Dynamic Lissajous harmonic orbital motion across frames via overlay eval=frame
        phase = recipe.recipe_params.get("phase", 0.5)
        fc = (
            f"[0:v]format={pix_fmt}[bg];"
            f"[bg][1:v]overlay=x='({width}-80)/2 + ({width}/4)*sin(2*PI*t*0.5+{phase})':"
            f"y='({height}-60)/2 + ({height}/4)*sin(3*PI*t*0.5)':eval=frame,format={pix_fmt}"
        )
        return [
            "-f", "lavfi", "-i", f"sierpinski=s={width}x{height}:r={fps}:seed={seed}",
            "-f", "lavfi", "-i", "color=c=cyan:s=80x60:r=" + str(fps),
            "-filter_complex", fc,
            "-t", str(duration),
        ], font_meta

    if t == "heldout_tex_chaoticshear":
        # Chaotic non-linear accelerated crop oscillation
        vf = (
            f"scale=trunc({width}*1.15/2)*2:trunc({height}*1.15/2)*2,"
            f"crop={width}:{height}:"
            f"x='(in_w-out_w)*(0.5+0.45*sin(t*t*0.08))':"
            f"y='(in_h-out_h)*(0.5+0.45*cos(t*1.7))',"
            f"format={pix_fmt}"
        )
        return ["-f", "lavfi", "-i", f"testsrc2=s={width}x{height}:r={fps}", "-vf", vf, "-t", str(duration)], font_meta

    # 2. Lines and text
    if t in ("lines_ticker", "lines_vertical_crawl", "heldout_lines_telemetry"):
        txt = recipe.recipe_params.get("text", "SMART SYNTHETIC TEST STREAM")
        spd = recipe.recipe_params.get("ticker_speed", 130)
        if has_drawtext:
            escaped_font = font_file.as_posix().replace(":", "\\:").replace("'", "\\'")
            if t == "lines_ticker":
                vf = (
                    f"drawbox=x=0:y={height-60}:w={width}:h=60:c=0x0a1428:t=fill,"
                    f"drawtext=fontfile='{escaped_font}':text='{txt}':fontsize=28:fontcolor=white:"
                    f"x='w-mod(t*{spd},w+text_w)':y={height-46},"
                    f"format={pix_fmt}"
                )
            elif t == "lines_vertical_crawl":
                vf = (
                    f"drawgrid=w=60:h=60:c=gray@0.3:t=1,"
                    f"drawtext=fontfile='{escaped_font}':text='{txt}':fontsize=24:fontcolor=lime:"
                    f"x=40:y='h-mod(t*50,h+text_h)',"
                    f"format={pix_fmt}"
                )
            else:  # heldout_lines_telemetry
                vf = (
                    f"drawgrid=w=120:h=120:c=cyan@0.4:t=1,"
                    f"drawbox=x={width//4}:y={height//4}:w={width//2}:h={height//2}:color=yellow@0.6:t=2,"
                    f"drawtext=fontfile='{escaped_font}':text='{txt}':fontsize=26:fontcolor=cyan:"
                    f"x=30:y=30,"
                    f"format={pix_fmt}"
                )
            return ["-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps}", "-vf", vf, "-t", str(duration)], font_meta
        else:
            overlay_png = work_dir / f"{recipe.id}_overlay.png"
            rasterize_text_overlay_pillow(
                font_file=font_file,
                text=txt,
                width=width,
                height=height,
                output_png=overlay_png,
                font_size=28,
            )
            fc = f"[0:v][1:v]overlay=0:0:shortest=1,format={pix_fmt}"
            return [
                "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps}",
                "-loop", "1", "-i", str(overlay_png),
                "-filter_complex", fc,
                "-t", str(duration),
            ], font_meta

    if t == "lines_grid_sweep":
        g_size = recipe.recipe_params.get("grid_size", 80)
        sw_spd = recipe.recipe_params.get("sweep_speed", 90)
        # Moving horizontal sweep line across stationary fine grid using overlay eval=frame
        fc = (
            f"[0:v]drawgrid=w={g_size}:h={g_size}:c=white@0.4:t=1,format={pix_fmt}[bg];"
            f"[bg][1:v]overlay=x=0:y='mod(t*{sw_spd},{height})':eval=frame,format={pix_fmt}"
        )
        return [
            "-f", "lavfi", "-i", f"color=c=0x080810:s={width}x{height}:r={fps}",
            "-f", "lavfi", "-i", f"color=c=yellow:s={width}x2:r={fps}",
            "-filter_complex", fc,
            "-t", str(duration),
        ], font_meta

    if t == "lines_crosshairs":
        rad = recipe.recipe_params.get("radius", 150)
        rot_spd = recipe.recipe_params.get("rot_speed", 1.8)
        # Rotating reticle target over grid background using overlay eval=frame
        fc = (
            f"[0:v]drawgrid=w=100:h=100:c=cyan@0.3:t=1,format={pix_fmt}[bg];"
            f"[bg][1:v]overlay=x='{width}/2-20+{rad}*cos(t*{rot_spd})':"
            f"y='{height}/2-20+{rad}*sin(t*{rot_spd})':eval=frame,format={pix_fmt}"
        )
        return [
            "-f", "lavfi", "-i", f"color=c=0x0a0a14:s={width}x{height}:r={fps}",
            "-f", "lavfi", "-i", "color=c=red:s=40x40:r=" + str(fps),
            "-filter_complex", fc,
            "-t", str(duration),
        ], font_meta

    if t == "heldout_lines_densegrid":
        vf = (
            f"drawgrid=w=24:h=24:c=white@0.5:t=1,"
            f"drawbox=x='mod(floor(t*10)*37,{width})':y='mod(floor(t*10)*41,{height})':w=24:h=24:color=yellow:t=fill,"
            f"format={pix_fmt}"
        )
        return ["-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps}", "-vf", vf, "-t", str(duration)], font_meta

    # 3. Dark gradients (native 10-bit evaluation BEFORE geq on 10-bit cases)
    if t == "dark_gradient_linear":
        invert = recipe.recipe_params.get("invert", False)
        if recipe.bit_depth == 10:
            c0_luma = recipe.recipe_params.get("c0_luma", 16) * 4
            c1_luma = recipe.recipe_params.get("c1_luma", 96) * 4
            expr = f"'{c1_luma}-(X/W)*({c1_luma}-{c0_luma})'" if invert else f"'{c0_luma}+(X/W)*({c1_luma}-{c0_luma})'"
            vf = f"geq=lum={expr}:cb=512:cr=512"
            return ["-f", "lavfi", "-i", f"nullsrc=s={width}x{height}:r={fps},format=yuv420p10le", "-vf", vf, "-t", str(duration)], font_meta
        else:
            c0_luma = recipe.recipe_params.get("c0_luma", 16)
            c1_luma = recipe.recipe_params.get("c1_luma", 96)
            expr = f"'{c1_luma}-(X/W)*({c1_luma}-{c0_luma})'" if invert else f"'{c0_luma}+(X/W)*({c1_luma}-{c0_luma})'"
            vf = f"geq=lum={expr}:cb=128:cr=128"
            return ["-f", "lavfi", "-i", f"nullsrc=s={width}x{height}:r={fps},format=yuv420p", "-vf", vf, "-t", str(duration)], font_meta

    if t == "dark_gradient_radial":
        if recipe.bit_depth == 10:
            c0_luma = recipe.recipe_params.get("c0_luma", 110) * 4
            c1_luma = recipe.recipe_params.get("c1_luma", 12) * 4
            vf = f"geq=lum='max({c1_luma},{c0_luma}-hypot(X-W/2,Y-H/2)/(W/2)*({c0_luma}-{c1_luma}))':cb=512:cr=512"
            return ["-f", "lavfi", "-i", f"nullsrc=s={width}x{height}:r={fps},format=yuv420p10le", "-vf", vf, "-t", str(duration)], font_meta
        else:
            c0_luma = recipe.recipe_params.get("c0_luma", 110)
            c1_luma = recipe.recipe_params.get("c1_luma", 12)
            vf = f"geq=lum='max({c1_luma},{c0_luma}-hypot(X-W/2,Y-H/2)/(W/2)*({c0_luma}-{c1_luma}))':cb=128:cr=128"
            return ["-f", "lavfi", "-i", f"nullsrc=s={width}x{height}:r={fps},format=yuv420p", "-vf", vf, "-t", str(duration)], font_meta

    if t == "dark_gradient_conic":
        c0 = recipe.recipe_params.get("c0", "0x020202")
        c1 = recipe.recipe_params.get("c1", "0x161616")
        grad = f"gradients=s={width}x{height}:r={fps}:c0={c0}:c1={c1}:type=circular:x0={width//2}:y0={height//2}:x1={width}:y1={height}:seed={seed}"
        return ["-f", "lavfi", "-i", grad, "-vf", f"format={pix_fmt}", "-t", str(duration)], font_meta

    if t == "heldout_dark_dithersteps":
        # True stepped quantization banding staircase (16 discrete levels) with micro-dither
        steps = recipe.recipe_params.get("steps", 16)
        vf = (
            f"geq=lum='64+floor((X/W)*{steps})*(384/{steps})+(mod(X*13+Y*17+{seed%7},3)-1)':"
            f"cb=512:cr=512"
        )
        return ["-f", "lavfi", "-i", f"nullsrc=s={width}x{height}:r={fps},format=yuv420p10le", "-vf", vf, "-t", str(duration)], font_meta

    if t == "heldout_dark_shadowcreep":
        grad = f"gradients=s={width}x{height}:r={fps}:c0=0x020202:c1=0x222222:type=radial:x0={width//2}:y0={height//2}:x1={width}:y1={height}:speed=0.06:seed={seed}"
        vf = f"noise=alls=10:allf=t+u:all_seed={seed},format={pix_fmt}"
        return ["-f", "lavfi", "-i", grad, "-vf", vf, "-t", str(duration)], font_meta

    # 4. Film noise (bound every noise filter all_seed to recipe.seed)
    if t == "film_noise_fine":
        strn = recipe.recipe_params.get("strength", 16)
        vf = f"noise=alls={strn}:allf=t+u:all_seed={seed},format={pix_fmt}"
        return ["-f", "lavfi", "-i", f"color=c=gray:s={width}x{height}:r={fps}", "-vf", vf, "-t", str(duration)], font_meta

    if t == "film_noise_coarse":
        l_s = recipe.recipe_params.get("luma_strength", 32)
        c_s = recipe.recipe_params.get("chroma_strength", 6)
        vf = f"noise=c0s={l_s}:c0f=t+u:c1s={c_s}:c2s={c_s}:all_seed={seed},boxblur=1:1,format={pix_fmt}"
        return ["-f", "lavfi", "-i", f"color=c=0x404040:s={width}x{height}:r={fps}", "-vf", vf, "-t", str(duration)], font_meta

    if t == "film_noise_speckle":
        strn = recipe.recipe_params.get("strength", 24)
        vf = f"noise=alls={strn}:allf=t+p:all_seed={seed},format={pix_fmt}"
        return ["-f", "lavfi", "-i", f"color=c=0x303030:s={width}x{height}:r={fps}", "-vf", vf, "-t", str(duration)], font_meta

    if t == "film_noise_chroma":
        l_s = recipe.recipe_params.get("luma_strength", 12)
        c_s = recipe.recipe_params.get("chroma_strength", 38)
        vf = f"noise=c0s={l_s}:c1s={c_s}:c2s={c_s}:allf=t+u:all_seed={seed},format={pix_fmt}"
        return ["-f", "lavfi", "-i", f"color=c=0x505050:s={width}x{height}:r={fps}", "-vf", vf, "-t", str(duration)], font_meta

    if t == "heldout_noise_burstysalt":
        vf = f"noise=alls=12:allf=t+u:all_seed={seed},noise=alls=28:allf=t+u:all_seed={seed+1}:enable='gt(mod(t,5),3.5)',format={pix_fmt}"
        return ["-f", "lavfi", "-i", f"color=c=0x353535:s={width}x{height}:r={fps}", "-vf", vf, "-t", str(duration)], font_meta

    if t == "heldout_noise_analogstreak":
        vf = (
            f"noise=alls=18:allf=t+u:all_seed={seed},"
            f"drawbox=x='mod(floor(t*12)*97,{width})':y=0:w=2:h={height}:color=white@0.6:t=fill,"
            f"format={pix_fmt}"
        )
        return ["-f", "lavfi", "-i", f"color=c=0x252525:s={width}x{height}:r={fps}", "-vf", vf, "-t", str(duration)], font_meta

    # 5. Transitions
    if t == "transition_xfade":
        tr = recipe.recipe_params.get("transition", "fade")
        dur = min(2.0, duration * 0.4)
        offset = max(0.1, duration / 2 - dur / 2)
        fc = f"xfade=transition={tr}:duration={dur}:offset={offset},format={pix_fmt}"
        return [
            "-f", "lavfi", "-i", f"color=c=0x152545:s={width}x{height}:r={fps}",
            "-f", "lavfi", "-i", f"color=c=0x551525:s={width}x{height}:r={fps}",
            "-filter_complex", fc,
            "-t", str(duration),
        ], font_meta

    if t == "heldout_trans_strobe":
        vf = f"eq=brightness='0.8*gt(sin(2*PI*t*15),0.5)':enable='between(t,{ev_start},{ev_end})',format={pix_fmt}"
        return ["-f", "lavfi", "-i", f"testsrc2=s={width}x{height}:r={fps}", "-vf", vf, "-t", str(duration)], font_meta

    if t == "heldout_trans_splitpush":
        dur = min(2.0, duration * 0.4)
        offset = max(0.1, duration / 2 - dur / 2)
        fc = f"xfade=transition=wiperight:duration={dur}:offset={offset},hue=s='if(between(t,{offset},{offset+dur}),0.2,1.0)',format={pix_fmt}"
        return [
            "-f", "lavfi", "-i", f"testsrc=s={width}x{height}:r={fps}",
            "-f", "lavfi", "-i", f"color=c=darkblue:s={width}x{height}:r={fps}",
            "-filter_complex", fc,
            "-t", str(duration),
        ], font_meta

    # 6. Short difficult events & Long rare events
    if t == "event_flash":
        vf = f"eq=brightness=0.75:enable='between(t,{ev_start},{ev_end})',format={pix_fmt}"
        return ["-f", "lavfi", "-i", f"color=c=0x141414:s={width}x{height}:r={fps}", "-vf", vf, "-t", str(duration)], font_meta

    if t == "event_glitch":
        vf = f"noise=alls=90:allf=t+u:all_seed={seed}:enable='between(t,{ev_start},{ev_end})',format={pix_fmt}"
        return ["-f", "lavfi", "-i", f"testsrc2=s={width}x{height}:r={fps}", "-vf", vf, "-t", str(duration)], font_meta

    if t == "event_particle":
        spd = recipe.recipe_params.get("speed", 400)
        # Moving particle across frames using overlay eval=frame
        fc = (
            f"[0:v]format={pix_fmt}[bg];"
            f"[bg][1:v]overlay=x='mod((t-{ev_start})*{spd},{width})':y='{height}-80':"
            f"eval=frame:enable='between(t,{ev_start},{ev_end})',format={pix_fmt}"
        )
        return [
            "-f", "lavfi", "-i", f"color=c=0x181818:s={width}x{height}:r={fps}",
            "-f", "lavfi", "-i", "color=c=white:s=40x40:r=" + str(fps),
            "-filter_complex", fc,
            "-t", str(duration),
        ], font_meta

    if t == "event_pulse":
        vf = f"eq=contrast=2.2:enable='between(t,{ev_start},{ev_end})',format={pix_fmt}"
        grad = f"gradients=s={width}x{height}:r={fps}:c0=0x080808:c1=0x303030:type=radial:x0={width//2}:y0={height//2}:x1={width}:y1={height}:seed={seed}"
        return ["-f", "lavfi", "-i", grad, "-vf", vf, "-t", str(duration)], font_meta

    if t == "heldout_event_microtransient":
        spd = recipe.recipe_params.get("occlusion_speed", 400)
        fc = (
            f"[0:v]format={pix_fmt}[bg];"
            f"[bg][1:v]overlay=x='{width}/3+(t-{ev_start})*{spd}':y='{height}/3':"
            f"eval=frame:enable='between(t,{ev_start},{ev_end})',format={pix_fmt}"
        )
        return [
            "-f", "lavfi", "-i", f"testsrc=s={width}x{height}:r={fps}",
            "-f", "lavfi", "-i", "color=c=red:s=80x80:r=" + str(fps),
            "-filter_complex", fc,
            "-t", str(duration),
        ], font_meta

    if t == "heldout_event_compoundburst":
        vf = (
            f"eq=brightness=0.65:enable='between(t,{ev_start},{ev_end})',"
            f"noise=alls=80:allf=t+u:all_seed={seed}:enable='between(t,{ev_start},{ev_end})',"
            f"format={pix_fmt}"
        )
        return ["-f", "lavfi", "-i", f"color=c=0x1c1c1c:s={width}x{height}:r={fps}", "-vf", vf, "-t", str(duration)], font_meta

    # Long rare events
    if t == "long_rare_static_needle":
        fc = (
            f"[0:v]format={pix_fmt}[bg];"
            f"[bg][1:v]overlay=x='{width}/2-2+(t-{ev_start})*40':y='{height}/4':"
            f"eval=frame:enable='between(t,{ev_start},{ev_end})',format={pix_fmt}"
        )
        return [
            "-f", "lavfi", "-i", f"color=c=0x101010:s={width}x{height}:r={fps}",
            "-f", "lavfi", "-i", f"color=c=white:s=4x{height//2}:r={fps}",
            "-filter_complex", fc,
            "-t", str(duration),
        ], font_meta

    if t == "long_rare_dark_flare":
        vf = f"eq=brightness=0.5:enable='between(t,{ev_start},{ev_end})',format={pix_fmt}"
        grad = f"gradients=s={width}x{height}:r={fps}:c0=0x020202:c1=0x181818:type=linear:x0=0:y0=0:x1={width}:y1={height}:seed={seed}"
        return ["-f", "lavfi", "-i", grad, "-vf", vf, "-t", str(duration)], font_meta

    if t == "long_rare_noise_burst":
        vf = f"noise=alls=60:allf=t+u:all_seed={seed}:enable='between(t,{ev_start},{ev_end})',format={pix_fmt}"
        return ["-f", "lavfi", "-i", f"color=c=0x303030:s={width}x{height}:r={fps}", "-vf", vf, "-t", str(duration)], font_meta

    if t == "long_rare_gradient_step":
        # True gradient banding step shift stimulus: shifts from continuous 10-bit gradient into 4-step quantization
        step_levels = recipe.recipe_params.get("step_levels", 4)
        vf = (
            f"geq=lum='if(gt(T,{ev_start})*lt(T,{ev_end}),"
            f"64+floor((X/W)*{step_levels})*(256/{step_levels}),64+(X/W)*256)':"
            f"cb=512:cr=512"
        )
        return ["-f", "lavfi", "-i", f"nullsrc=s={width}x{height}:r={fps},format=yuv420p10le", "-vf", vf, "-t", str(duration)], font_meta

    if t == "long_rare_texture_surge":
        vf = f"eq=contrast=2.5:enable='between(t,{ev_start},{ev_end})',format={pix_fmt}"
        return ["-f", "lavfi", "-i", f"testsrc2=s={width}x{height}:r={fps}", "-vf", vf, "-t", str(duration)], font_meta

    if t == "long_rare_vector_glitch":
        vf = f"drawgrid=w=20:h=20:c=cyan@0.8:t=2:enable='between(t,{ev_start},{ev_end})',format={pix_fmt}"
        return ["-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps}", "-vf", vf, "-t", str(duration)], font_meta

    # Strictly fail closed on unknown or unhandled recipe types
    raise ValueError(f"Unknown or unhandled recipe type: {recipe.recipe_type!r} in case {recipe.id}")


def run_ffmpeg(cmd: Sequence[str], label: str) -> None:
    """Execute an FFmpeg command with explicit argv, raising on any error."""
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        cmd_str = " ".join(str(c) for c in cmd)
        raise RuntimeError(
            f"FFmpeg failed for {label} (exit code {completed.returncode}):\n"
            f"Command: {cmd_str}\n"
            f"Stderr:\n{completed.stderr}"
        )


def run_ffprobe(ffprobe: Path, video_path: Path) -> dict[str, Any]:
    """Execute FFprobe on a video file and return format and stream details."""
    cmd = [
        str(ffprobe),
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(video_path),
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"FFprobe failed for {video_path} (exit code {completed.returncode}):\n{completed.stderr}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"FFprobe produced invalid JSON for {video_path}: {exc}") from exc


def compute_sha256(path: Path) -> str:
    """Compute SHA256 hex digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def render_h264_derivative(
    ffmpeg: Path,
    ffprobe: Path,
    source_mkv: Path,
    output_mp4: Path,
    fps: int,
) -> dict[str, Any]:
    """Encode an optional long-GOP H.264 derivative via libx264 CPU encoder."""
    gop = max(fps * 2, 48)
    cmd = [
        str(ffmpeg),
        "-y",
        "-i",
        str(source_mkv),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-g",
        str(gop),
        "-keyint_min",
        str(fps),
        "-sc_threshold",
        "0",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "20",
        str(output_mp4),
    ]
    run_ffmpeg(cmd, label=f"H264 derivative {output_mp4.name}")
    probe = run_ffprobe(ffprobe, output_mp4)
    return {
        "output_file": str(output_mp4),
        "sha256": compute_sha256(output_mp4),
        "size_bytes": output_mp4.stat().st_size,
        "gop_size": gop,
        "codec": "h264",
        "render_argv": cmd,
        "ffprobe": probe,
    }


def render_hevc_derivative(
    ffmpeg: Path,
    ffprobe: Path,
    source_mkv: Path,
    output_mp4: Path,
    fps: int,
) -> dict[str, Any]:
    """Encode an optional long-GOP HEVC derivative via libx265 CPU encoder."""
    keyint = fps * 10
    cmd = [
        str(ffmpeg),
        "-y",
        "-i",
        str(source_mkv),
        "-c:v",
        "libx265",
        "-x265-params",
        f"keyint={keyint}:min-keyint={keyint}:scenecut=0",
        "-crf",
        "24",
        "-pix_fmt",
        "yuv420p",
        str(output_mp4),
    ]
    run_ffmpeg(cmd, label=f"HEVC derivative {output_mp4.name}")
    probe = run_ffprobe(ffprobe, output_mp4)
    return {
        "output_file": str(output_mp4),
        "sha256": compute_sha256(output_mp4),
        "size_bytes": output_mp4.stat().st_size,
        "keyint": keyint,
        "codec": "hevc",
        "render_argv": cmd,
        "ffprobe": probe,
    }


def get_tool_identity(ffmpeg: Path, ffprobe: Path, font_file: Path | None) -> dict[str, Any]:
    """Return tool metadata including binary versions and font info with font SHA."""
    ffmpeg_ver = "unknown"
    ffprobe_ver = "unknown"
    try:
        f_proc = subprocess.run([str(ffmpeg), "-version"], capture_output=True, text=True, check=False)
        if f_proc.returncode == 0 and f_proc.stdout:
            ffmpeg_ver = f_proc.stdout.splitlines()[0]
    except OSError:
        pass

    try:
        p_proc = subprocess.run([str(ffprobe), "-version"], capture_output=True, text=True, check=False)
        if p_proc.returncode == 0 and p_proc.stdout:
            ffprobe_ver = p_proc.stdout.splitlines()[0]
    except OSError:
        pass

    identity: dict[str, Any] = {
        "generator": "scripts/generate_smart_corpus.py",
        "generator_version": "1.0.0",
        "ffmpeg_path": str(ffmpeg),
        "ffmpeg_version": ffmpeg_ver,
        "ffprobe_path": str(ffprobe),
        "ffprobe_version": ffprobe_ver,
        "font_file": str(font_file) if font_file else None,
        "font_name": font_file.stem if font_file else None,
        "font_sha256": compute_sha256(font_file) if font_file and font_file.is_file() else None,
    }
    return identity


def _filter_recipes(split: str, limit: int | None) -> tuple[CaseRecipe, ...]:
    """Filter recipe list by split and limit."""
    if split == "all":
        selected = list(ALL_RECIPES)
    else:
        selected = [r for r in ALL_RECIPES if r.split == split]
    if limit is not None and limit > 0:
        selected = selected[:limit]
    return tuple(selected)


def build_manifest_and_render(
    *,
    ffmpeg: Path,
    ffprobe: Path,
    output_dir: Path,
    font_file: Path,
    split: str,
    limit: int | None,
    render: bool,
    smoke_width: int | None,
    smoke_height: int | None,
    smoke_duration: float | None,
    generate_h264: bool,
    generate_hevc: bool = False,
    force_pillow: bool = False,
) -> dict[str, Any]:
    """Generate the corpus manifest and optionally render the video assets."""
    if ffmpeg is None:
        raise ValueError("An explicit FFmpeg binary path must be provided via --ffmpeg")
    if ffprobe is None:
        raise ValueError("An explicit FFprobe binary path must be provided via --ffprobe")
    if font_file is None:
        raise ValueError("An explicit font file must be provided via --font-file")

    resolved_ffmpeg = Path(ffmpeg).expanduser().resolve()
    resolved_ffprobe = Path(ffprobe).expanduser().resolve()
    resolved_font = resolve_font_file(font_file)

    if render:
        if not resolved_ffmpeg.is_file():
            raise FileNotFoundError(f"FFmpeg binary not found at: {resolved_ffmpeg}")
        if not resolved_ffprobe.is_file():
            raise FileNotFoundError(f"FFprobe binary not found at: {resolved_ffprobe}")

    output_dir.mkdir(parents=True, exist_ok=True)
    recipes = _filter_recipes(split, limit)

    has_smoke_overrides = bool(
        smoke_width is not None or smoke_height is not None or smoke_duration is not None
    )

    has_drawtext = False if force_pillow else detect_drawtext(resolved_ffmpeg)

    tool_identity = get_tool_identity(resolved_ffmpeg, resolved_ffprobe, resolved_font)
    cases_manifest: list[dict[str, Any]] = []

    runner_script = (REPOSITORY_ROOT / "scripts" / "run_smart_case.py").resolve()

    for recipe in recipes:
        duration = smoke_duration if smoke_duration is not None else recipe.duration_sec
        width = smoke_width if smoke_width is not None else recipe.width
        height = smoke_height if smoke_height is not None else recipe.height

        events = recipe.event_times
        if smoke_duration is not None and recipe.event_times:
            events = _scale_event_times(recipe.event_times, recipe.duration_sec, smoke_duration)

        video_filename = f"{recipe.id}.mkv"
        target_video_path = output_dir / video_filename

        filter_args, font_meta = build_ffmpeg_filter_args(
            recipe=recipe,
            font_file=resolved_font,
            has_drawtext=has_drawtext,
            work_dir=output_dir,
            smoke_duration=smoke_duration,
            smoke_width=smoke_width,
            smoke_height=smoke_height,
        )

        cmd = [
            str(resolved_ffmpeg),
            "-y",
            *filter_args,
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-threads",
            "0",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
            str(target_video_path),
        ]

        render_info: dict[str, Any] = {
            "rendered": False,
            "output_file": str(target_video_path),
            "render_argv": cmd if render else None,
            "sha256": None,
            "size_bytes": None,
        }

        if render:
            run_ffmpeg(cmd, label=f"FFV1 render {recipe.id}")
            probe_result = run_ffprobe(resolved_ffprobe, target_video_path)
            file_sha = compute_sha256(target_video_path)
            file_size = target_video_path.stat().st_size

            render_info["rendered"] = True
            render_info["sha256"] = file_sha
            render_info["size_bytes"] = file_size
            render_info["ffprobe"] = probe_result
            render_info["font_render"] = font_meta

            if generate_h264:
                h264_filename = f"{recipe.id}.h264.mp4"
                h264_path = output_dir / h264_filename
                h264_info = render_h264_derivative(
                    ffmpeg=resolved_ffmpeg,
                    ffprobe=resolved_ffprobe,
                    source_mkv=target_video_path,
                    output_mp4=h264_path,
                    fps=recipe.fps,
                )
                render_info["h264_derivative"] = h264_info

            if generate_hevc:
                hevc_filename = f"{recipe.id}.hevc.mp4"
                hevc_path = output_dir / hevc_filename
                hevc_info = render_hevc_derivative(
                    ffmpeg=resolved_ffmpeg,
                    ffprobe=resolved_ffprobe,
                    source_mkv=target_video_path,
                    output_mp4=hevc_path,
                    fps=recipe.fps,
                )
                render_info["hevc_derivative"] = hevc_info

        metadata: dict[str, Any] = {
            "split": recipe.split,
            "group": recipe.group,
            "scene_family": recipe.scene_family,
            "seed": recipe.seed,
            "width": width,
            "height": height,
            "fps": recipe.fps,
            "bit_depth": recipe.bit_depth,
            "pix_fmt": recipe.pix_fmt,
            "duration_sec": duration,
            "event_times": list(events),
            "smoke": has_smoke_overrides,
            "acceptance_valid": not has_smoke_overrides and recipe.split == "acceptance",
            "recipe": {
                "type": recipe.recipe_type,
                "params": recipe.recipe_params,
            },
            "tool_identity": tool_identity,
            "render": render_info,
        }

        # Runner command template as required by reviewer:
        # sys.executable, absolute scripts/run_smart_case.py, --source {source} --ffmpeg {ffmpeg} --ffprobe {ffprobe} --workdir {case_dir} --result {result_path}, omit case-id.
        runner_command = [
            sys.executable,
            str(runner_script),
            "--source",
            "{source}",
            "--ffmpeg",
            "{ffmpeg}",
            "--ffprobe",
            "{ffprobe}",
            "--workdir",
            "{case_dir}",
            "--result",
            "{result_path}",
        ]

        cases_manifest.append(
            {
                "id": recipe.id,
                "source": video_filename,
                "command": runner_command,
                "metadata": metadata,
            }
        )

    manifest_payload = {
        "schema_version": 1,
        "cases": cases_manifest,
    }

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_payload, indent=2), encoding="utf-8")
    return manifest_payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate deterministic synthetic Smart video corpus and evaluation manifest."
    )
    parser.add_argument(
        "--ffmpeg",
        type=Path,
        required=True,
        help="Exact ffmpeg binary path",
    )
    parser.add_argument(
        "--ffprobe",
        type=Path,
        required=True,
        help="Exact ffprobe binary path",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPOSITORY_ROOT / "workdir" / "smart-corpus",
        help="Output directory for generated manifest and videos",
    )
    parser.add_argument(
        "--font-file",
        type=Path,
        required=True,
        help="Explicit font file for text overlays (TTF/OTF/TTC)",
    )
    parser.add_argument(
        "--split",
        choices=SPLIT_CHOICES,
        default="all",
        help="Corpus split to generate (default: all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of cases generated",
    )
    parser.add_argument(
        "--render",
        action="store_true",
        help="Render video assets via FFmpeg (default: recipe manifest only)",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=None,
        help="Smoke test override for frame width (recorded as smoke, not acceptance)",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=None,
        help="Smoke test override for frame height (recorded as smoke, not acceptance)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Smoke test override for clip duration in seconds (recorded as smoke, not acceptance)",
    )
    parser.add_argument(
        "--h264-derivative",
        action="store_true",
        help="Also render optional long-GOP H264 derivative via libx264 CPU",
    )
    parser.add_argument(
        "--hevc-derivative",
        action="store_true",
        help="Also render optional long-GOP HEVC derivative via libx265 CPU",
    )
    parser.add_argument(
        "--pillow-text",
        action="store_true",
        help="Force Pillow rasterized text overlay fallback instead of drawtext filter",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    ffmpeg = args.ffmpeg.expanduser().resolve()
    ffprobe = args.ffprobe.expanduser().resolve()
    font_file = args.font_file.expanduser().resolve()

    if not ffmpeg.is_file():
        sys.stderr.write(f"Error: FFmpeg binary not found at {ffmpeg}\n")
        return 1
    if not ffprobe.is_file():
        sys.stderr.write(f"Error: FFprobe binary not found at {ffprobe}\n")
        return 1
    if not font_file.is_file():
        sys.stderr.write(f"Error: Font file not found at {font_file}\n")
        return 1

    try:
        manifest = build_manifest_and_render(
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            output_dir=args.output_dir,
            font_file=args.font_file,
            split=args.split,
            limit=args.limit,
            render=args.render,
            smoke_width=args.width,
            smoke_height=args.height,
            smoke_duration=args.duration,
            generate_h264=args.h264_derivative,
            generate_hevc=args.hevc_derivative,
            force_pillow=args.pillow_text,
        )
    except Exception as exc:
        sys.stderr.write(f"Corpus generation failed: {exc}\n")
        return 1

    case_count = len(manifest.get("cases", []))
    rendered_str = "rendered video files" if args.render else "recipe manifest only"
    print(
        f"Corpus generated successfully ({case_count} cases, split={args.split}, mode={rendered_str})."
    )
    print(f"Manifest written to: {args.output_dir / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
