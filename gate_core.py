"""
gate_core — UI-drivable core for DaVinci Gate.

The pipeline is split into three callables so a UI (or a headless caller) can
drive it stage-by-stage:

    analyze(resolve, settings, progress, cancel) -> Plan | None
        Discover hosts, render per-clip audio, detect silence. NO timeline writes.

    summarize(plan) -> list[SpeakerSummary]
        Cheap, pure. Feeds a preview table before the commit.

    commit(resolve, plan, settings, progress, cancel) -> Result
        Build the [Processed] tracks and disable silence clips.

    run_headless(resolve, settings, progress, cancel) -> Result | None
        Convenience wrapper: analyze -> commit, with stdout progress by default.

Constraints (kept intact from v4):
    - Stdlib + DaVinci Resolve scripting API only.
    - Resolve's scripting API often signals failure by returning False/None
      rather than raising, so success is measured by the return value.
    - The chunked AppendToTimeline + handle-refresh pattern is a deliberate
      workaround for MediaPool pointer invalidation between batches.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from detect_silence import detect_silence


Progress = Callable[[Optional[float], str], None]
Cancel = Callable[[], bool]


def _default_progress(fraction: Optional[float], message: str) -> None:
    print(f">>> {message}")


def _default_cancel() -> bool:
    return False


class Cancelled(Exception):
    """Raised inside long loops when the caller signals cancel()."""


@dataclass
class GateSettings:
    silence_threshold_db: float = -50.0
    min_silence_ms: int = 600
    padding_ms: int = 120
    hold_ms: int = 500
    # Post-detection cleanup: silence gaps shorter than this get merged into
    # the surrounding speech instead of becoming their own clip. Prevents the
    # "4-frame gap in the middle of a sentence" artifact caused by
    # padding + hold eating into originally-longer silences.
    min_gated_ms: int = 700
    batch_size: int = 250
    render_preset: str = "AudioOnly_IndividualClips"
    fps_hint: float = 30.0
    track_name_normalize: bool = True
    temp_dir: Optional[str] = None
    selected_tracks: Optional[set] = None  # None = every audio track
    use_cache: bool = True
    cache_dir: Optional[str] = None  # None = platform-default location
    auto_calibrate: bool = True
    # Strictness controls where the auto threshold sits between the noise
    # floor (0.0) and the "speech level" percentile (1.0). Higher = stricter
    # = gates more (threshold closer to actual speech). Works across audio
    # profiles because it uses each host's measured dynamic range.
    strictness: float = 0.75
    # Percentile used as the "speech level" reference. P70 is a robust proxy
    # for the low edge of continuous speech (below it is silence + breath).
    speech_percentile: int = 70
    host_thresholds: Optional[dict] = None  # explicit per-host overrides; takes precedence


@dataclass
class Plan:
    """Output of analyze(); consumed by summarize() and commit(). No writes yet."""

    hosts: list
    host_wavs: dict
    host_jsons: dict
    fps: float
    outdir: str  # working dir for JSONs (and non-cached WAVs); cleaned by run_headless
    cache_hits: int = 0
    cache_misses: int = 0
    cache_dir: Optional[str] = None
    host_noise_floors: dict = field(default_factory=dict)  # host_name -> dBFS
    host_thresholds_used: dict = field(default_factory=dict)  # host_name -> dBFS
    host_speech_levels: dict = field(default_factory=dict)  # host_name -> dBFS at speech percentile


@dataclass
class SpeakerSummary:
    host_name: str
    compound_label: str
    n_segments: int
    n_silence_segments: int
    n_speech_segments: int
    pct_disabled: float
    total_gated_ms: int
    total_duration_ms: int
    noise_floor_db: Optional[float] = None
    threshold_db: Optional[float] = None
    speech_level_db: Optional[float] = None


@dataclass
class Result:
    disabled_count: int = 0
    disabled_by_method: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    tracks_created: int = 0
    cancelled: bool = False


DISABLE_METHODS_VISUAL = ("SetClipEnabled", "Enabled", "AudioEnabled")


def disable_timeline_clip(clip) -> tuple:
    """Disable a timeline clip; returns ``(method, is_visual_disable)``.

    ``method`` is the API call that succeeded, or ``None`` if all failed.
    ``is_visual_disable`` is True when the clip is greyed out on the timeline,
    False for the ``Volume->0.0`` fallback (silent but visually enabled — this
    breaks the checkerboard-review property, so callers must warn loudly).

    The plain try/except pattern in v4 treated a False return as success. We
    check the return value here so a silent API failure doesn't inflate the
    disabled-count while leaving clips playing.
    """
    attempts = (
        ("SetClipEnabled", lambda: clip.SetClipEnabled(False)),
        ("Enabled", lambda: clip.SetProperty("Enabled", False)),
        ("AudioEnabled", lambda: clip.SetProperty("AudioEnabled", False)),
        ("Volume", lambda: clip.SetProperty("Volume", 0.0)),
    )
    for name, fn in attempts:
        try:
            ok = fn()
        except Exception:
            continue
        if ok:
            return name, name in DISABLE_METHODS_VISUAL
    return None, False


def refresh_handles(resolve_obj):
    """Switch to Edit page and re-fetch project/timeline/mediapool handles."""
    resolve_obj.OpenPage("edit")
    time.sleep(0.5)
    p = resolve_obj.GetProjectManager().GetCurrentProject()
    if not p:
        raise RuntimeError("Could not get current project")
    tl = p.GetCurrentTimeline()
    if not tl:
        raise RuntimeError("Could not get current timeline")
    mp = p.GetMediaPool()
    if not mp:
        raise RuntimeError("Could not get media pool")
    return p, tl, mp


def refresh_pool_handles(resolve_obj):
    """Re-fetch project/timeline/mediapool without switching pages."""
    p = resolve_obj.GetProjectManager().GetCurrentProject()
    if not p:
        raise RuntimeError("Could not get current project")
    tl = p.GetCurrentTimeline()
    if not tl:
        raise RuntimeError("Could not get current timeline")
    mp = p.GetMediaPool()
    if not mp:
        raise RuntimeError("Could not get media pool")
    return p, tl, mp


def normalize_name(raw: str) -> str:
    return raw.strip().title()


def _fs_safe_stem(s: str, max_len: int = 56) -> str:
    """ASCII-ish, filesystem-safe basename for JSON sidecars."""
    s = re.sub(r"[^\w\-]+", "_", s.strip(), flags=re.UNICODE).strip("_")
    return (s or "clip")[:max_len]


def discover_hosts(tl, settings: Optional[GateSettings] = None) -> list:
    """Find audio clips on audio tracks; one host per clip with a unique JSON stem."""
    if settings is None:
        settings = GateSettings()

    hosts: list = []
    per_track_stem_count: dict = defaultdict(int)

    for i in range(1, tl.GetTrackCount("audio") + 1):
        if settings.selected_tracks is not None and i not in settings.selected_tracks:
            continue
        items = tl.GetItemListInTrack("audio", i) or []
        for item in items:
            try:
                raw_name = item.GetName()
                if not raw_name or not raw_name.strip():
                    continue
                clean = raw_name.strip()
                compound_label = normalize_name(clean) if settings.track_name_normalize else clean
                start_f = int(item.GetStart())
                stem = _fs_safe_stem(compound_label)
                per_track_stem_count[(i, stem)] += 1
                n = per_track_stem_count[(i, stem)]
                json_name = f"A{i:02d}_{stem}" + (f"_{n}" if n > 1 else "")
                hosts.append(
                    {
                        "name": json_name,
                        "clip": clean,
                        "compound_label": compound_label,
                        "track": i,
                        "item": item,
                        "start_f": start_f,
                    }
                )
            except Exception:
                continue

    if not hosts:
        raise RuntimeError(
            "No audio tracks with clips found. Ensure your timeline has audio tracks with named clips."
        )
    return hosts


def append_in_chunks(
    infos,
    mp,
    resolve=None,
    size: int = 250,
    progress: Progress = _default_progress,
    cancel: Cancel = _default_cancel,
    progress_base: Optional[float] = None,
    progress_span: float = 0.0,
):
    """Append timeline items in chunks; refetch MediaPool between chunks.

    Resolve often invalidates the MediaPool pointer after AppendToTimeline,
    which makes the *next* append ignore ``trackIndex`` or return fewer items
    than requested (the "second speaker" glitch). Re-fetching handles between
    chunks avoids this.
    """
    out: list = []
    if not infos:
        return out
    n_chunks = (len(infos) + size - 1) // size
    for ci, i in enumerate(range(0, len(infos), size), start=1):
        if cancel():
            raise Cancelled()
        chunk = infos[i : i + size]
        if resolve is not None and i > 0:
            _, _, mp = refresh_pool_handles(resolve)
            time.sleep(0.05)
        result = mp.AppendToTimeline(chunk) or []
        if len(result) != len(chunk):
            progress(
                None,
                f"WARNING: AppendToTimeline returned {len(result)}/{len(chunk)} items (chunk {ci}/{n_chunks})",
            )
        out.extend(result)
        if progress_base is not None and progress_span > 0:
            progress(
                progress_base + progress_span * ci / n_chunks,
                f"Appended chunk {ci}/{n_chunks} ({len(chunk)} items)",
            )
        else:
            progress(None, f"Appended chunk {ci}/{n_chunks} ({len(chunk)} items)")
    return out


def load_segments(json_path: str, fps: float):
    """Load segment tuples ``(startF, endF, is_silence)`` from a detect_silence JSON."""
    with open(json_path, "r") as f:
        segs = json.load(f)
    out: list = []
    for s in segs:
        sF = int(s.get("startF", s.get("start_sec", 0) * fps))
        eF = int(s.get("endF", s.get("end_sec", 0) * fps))
        if eF > sF:
            out.append((sF, eF, s.get("is_silence", False)))
    return out


_RENDER_SUFFIX_RE = re.compile(r"^(.*?)[_-]?\d{4,}$")


def _strip_render_suffix(basename: str) -> str:
    """Strip Resolve's per-clip render numeric suffix (e.g. ``_00000000``)."""
    stem, _ = os.path.splitext(basename)
    m = _RENDER_SUFFIX_RE.match(stem)
    if m:
        stripped = m.group(1)
        if stripped:
            return stripped
    return stem


def resolve_render_outputs(hosts: list, new_wavs: list) -> tuple:
    """Match new WAV files to hosts by basename (v4's suffix-guessing rewrite).

    Uses a set-diff (caller passes only WAVs that appeared during this render)
    and matches on the base filename with numeric render suffix stripped, first
    against ``host['clip']`` (Resolve's default naming source) then against
    ``host['name']``. Falls back to a prefix match once per host. Anything
    unmatched is reported so ``analyze()`` can fail loudly with actionable info.

    Returns ``(host_wavs, unmatched_hosts, orphan_wavs)``.
    """
    host_wavs: dict = {}
    used_wavs: set = set()

    def _key(name: str) -> str:
        return _strip_render_suffix(name).strip().lower()

    wav_index: dict = defaultdict(list)
    for w in new_wavs:
        wav_index[_key(os.path.basename(w))].append(w)

    for host in hosts:
        clip_key = host["clip"].strip().lower()
        name_key = host["name"].strip().lower()
        chosen = None
        for k in (clip_key, name_key):
            for w in wav_index.get(k, []):
                if w not in used_wavs:
                    chosen = w
                    break
            if chosen:
                break
        if not chosen:
            for w in new_wavs:
                if w in used_wavs:
                    continue
                base_key = _key(os.path.basename(w))
                if not base_key or not clip_key:
                    continue
                if base_key.startswith(clip_key) or clip_key.startswith(base_key):
                    chosen = w
                    break
        if chosen:
            host_wavs[host["name"]] = chosen
            used_wavs.add(chosen)

    unmatched = [h["name"] for h in hosts if h["name"] not in host_wavs]
    orphans = [w for w in new_wavs if w not in used_wavs]
    return host_wavs, unmatched, orphans


def _default_cache_root() -> str:
    """Platform-appropriate persistent cache root for DaVinciGate WAV caches."""
    if sys.platform == "darwin":
        return os.path.expanduser(
            "~/Library/Application Support/Blackmagic Design/DaVinci Resolve/DaVinciGate/cache"
        )
    if sys.platform == "win32":
        return os.path.expandvars(
            r"%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\DaVinciGate\cache"
        )
    return os.path.expanduser("~/.local/share/DaVinciResolve/DaVinciGate/cache")


def _sanitize_dir_name(name: str) -> str:
    slug = re.sub(r"[^\w\-]+", "_", (name or "").strip(), flags=re.UNICODE).strip("_")
    return slug or "UnknownProject"


def _project_cache_dir(cache_root: str, proj) -> Optional[str]:
    """Return the per-project cache directory, creating it on demand. None on failure."""
    try:
        name = proj.GetName() if proj else None
    except Exception:
        name = None
    path = os.path.join(cache_root, _sanitize_dir_name(name or "UnknownProject"))
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        return None
    return path


def _host_cache_key(host: dict) -> str:
    """Stable 16-char hex key identifying this host's audio content.

    Includes the timeline item's identity (UniqueId when the build exposes it),
    its source in/out points and durations, and the underlying MediaPoolItem
    identity. Any relevant edit (trim, retime, source swap) changes the key and
    forces a fresh render.
    """
    item = host.get("item")
    parts = [
        f"clip={host.get('clip', '')}",
        f"label={host.get('compound_label', '')}",
        f"track={host.get('track', '')}",
    ]
    if item is not None:
        for attr in (
            "GetUniqueId",
            "GetStart",
            "GetEnd",
            "GetDuration",
            "GetLeftOffset",
            "GetRightOffset",
        ):
            try:
                fn = getattr(item, attr, None)
                v = fn() if callable(fn) else None
            except Exception:
                v = "err"
            parts.append(f"{attr}={v}")
        try:
            mpi = item.GetMediaPoolItem()
        except Exception:
            mpi = None
        if mpi is not None:
            for m_attr in ("GetMediaId", "GetUniqueId"):
                try:
                    fn = getattr(mpi, m_attr, None)
                    v = fn() if callable(fn) else None
                except Exception:
                    v = "err"
                parts.append(f"mpi.{m_attr}={v}")
        else:
            parts.append("mpi=none")
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()[:16]


def clear_project_cache(resolve, cache_dir: Optional[str] = None) -> Optional[str]:
    """Remove the WAV cache for the current project. Returns the cleared path, or None."""
    try:
        proj = resolve.GetProjectManager().GetCurrentProject()
    except Exception:
        return None
    if not proj:
        return None
    root = cache_dir or _default_cache_root()
    path = _project_cache_dir(root, proj)
    if not path:
        return None
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        return None
    return path


_STATS_PERCENTILES = (5, 10, 25, 50, 60, 70, 75, 80, 90, 95)
_STATS_VERSION = 2  # bump when the algorithm changes to invalidate old sidecars


def merge_short_silences_in_json(json_path: str, min_gated_ms: int) -> int:
    """Merge silence segments shorter than ``min_gated_ms`` into surrounding
    speech. Rewrites ``json_path`` in place. Returns the number of merges
    performed.

    Only merges *interior* short silences (not the leading or trailing edge
    of the timeline). This preserves whatever silence bracketing exists at
    the head/tail and only cleans up the "tiny gap in the middle of a
    sentence" artifact.
    """
    if min_gated_ms <= 0:
        return 0
    try:
        with open(json_path, "r") as f:
            segs = json.load(f)
    except Exception:
        return 0
    if not segs:
        return 0

    merges = 0
    i = 1
    while i < len(segs) - 1:
        s = segs[i]
        if s.get("is_silence"):
            dur_ms = (float(s["end_sec"]) - float(s["start_sec"])) * 1000.0
            if dur_ms < min_gated_ms:
                prev_s = segs[i - 1]
                next_s = segs[i + 1]
                if not prev_s.get("is_silence") and not next_s.get("is_silence"):
                    prev_s["end_sec"] = next_s["end_sec"]
                    if "endF" in next_s:
                        prev_s["endF"] = next_s["endF"]
                    del segs[i : i + 2]
                    merges += 1
                    continue
        i += 1

    if merges > 0:
        try:
            with open(json_path, "w") as f:
                json.dump(segs, f, indent=2)
        except Exception:
            pass
    return merges


def measure_audio_stats(wav_path: str, window_ms: int = 100) -> dict:
    """Sliding-window dBFS statistics for a WAV file.

    Returns a dict with per-percentile dBFS values (see ``_STATS_PERCENTILES``)
    plus min / max. One decode + one pass over the file gets us everything we
    need for both noise-floor and speech-level estimates.
    """
    from detect_silence import _chunk_dbfs, _load_wav

    samples, framerate, duration_ms, _, _ = _load_wav(wav_path)
    if duration_ms <= 0 or not samples:
        return {
            "window_ms": window_ms,
            "duration_ms": 0,
            "n_windows": 0,
            "min_db": -120.0,
            "max_db": -120.0,
            "percentiles": {p: -120.0 for p in _STATS_PERCENTILES},
        }
    dbs = []
    t = 0
    while t < duration_ms:
        end = min(duration_ms, t + window_ms)
        dbs.append(_chunk_dbfs(samples, framerate, t, end))
        t += window_ms
    dbs.sort()
    n = len(dbs)

    def _pct(p: int) -> float:
        idx = max(0, min(n - 1, int(n * p / 100.0)))
        return dbs[idx]

    return {
        "window_ms": window_ms,
        "duration_ms": duration_ms,
        "n_windows": n,
        "min_db": dbs[0],
        "max_db": dbs[-1],
        "percentiles": {p: _pct(p) for p in _STATS_PERCENTILES},
        "version": _STATS_VERSION,
    }


def cached_audio_stats(wav_path: str, window_ms: int = 100) -> dict:
    """Like :func:`measure_audio_stats` but persists a JSON sidecar next to
    the WAV so repeat calls on the same file are instant.
    """
    sidecar = wav_path + ".stats.json"
    if os.path.exists(sidecar):
        try:
            with open(sidecar, "r") as f:
                data = json.load(f)
            if (
                data.get("version") == _STATS_VERSION
                and data.get("window_ms") == window_ms
                and "percentiles" in data
            ):
                # JSON keys are strings; coerce back to ints for downstream.
                data["percentiles"] = {int(k): float(v) for k, v in data["percentiles"].items()}
                return data
        except Exception:
            pass
    stats = measure_audio_stats(wav_path, window_ms=window_ms)
    try:
        with open(sidecar, "w") as f:
            json.dump(stats, f)
    except Exception:
        pass
    return stats


def _percentile_from_stats(stats: dict, p: int) -> float:
    """Fetch a percentile from a stats dict, falling back to the nearest
    measured percentile if the exact one isn't stored.
    """
    pcts = stats.get("percentiles") or {}
    if p in pcts:
        return float(pcts[p])
    if not pcts:
        return -120.0
    nearest = min(pcts.keys(), key=lambda k: abs(k - p))
    return float(pcts[nearest])


def measure_noise_floor(wav_path: str, window_ms: int = 100, percentile: int = 10) -> float:
    """Backwards-compatible thin wrapper around :func:`measure_audio_stats`."""
    return _percentile_from_stats(
        measure_audio_stats(wav_path, window_ms=window_ms), percentile
    )


def cached_noise_floor(wav_path: str, window_ms: int = 100, percentile: int = 10) -> float:
    """Backwards-compatible thin wrapper around :func:`cached_audio_stats`."""
    return _percentile_from_stats(
        cached_audio_stats(wav_path, window_ms=window_ms), percentile
    )


def analyze(
    resolve,
    settings: GateSettings,
    progress: Progress = _default_progress,
    cancel: Cancel = _default_cancel,
) -> Optional[Plan]:
    """Render per-clip audio (or reuse the WAV cache) and detect silence.

    Returns an in-memory :class:`Plan`. Does NOT modify the timeline. Returns
    ``None`` if the caller cancels before a Plan can be built.

    Caching (when :attr:`GateSettings.use_cache` is True): each host has a
    content-addressed WAV in ``<cache_dir>/<project>/<key>.wav``. If every host
    hits, the Deliver render is skipped entirely. Any miss triggers a full
    render (Resolve's per-clip preset renders the whole timeline in one job),
    and each missed host's WAV is copied into the cache for next time.
    """
    if cancel():
        return None

    proj = resolve.GetProjectManager().GetCurrentProject()
    if not proj:
        raise RuntimeError("No project loaded")
    tl = proj.GetCurrentTimeline()
    if not tl:
        raise RuntimeError("No timeline loaded")

    outdir = settings.temp_dir or tempfile.mkdtemp(prefix="_temp_gate_")
    os.makedirs(outdir, exist_ok=True)

    try:
        progress(0.02, "Discovering hosts")
        hosts = discover_hosts(tl, settings)
        progress(0.05, f"Found {len(hosts)} host(s)")
        if cancel():
            return None

        cache_dir: Optional[str] = None
        host_cache_paths: dict = {}
        if settings.use_cache:
            cache_root = settings.cache_dir or _default_cache_root()
            cache_dir = _project_cache_dir(cache_root, proj)
            if cache_dir:
                for h in hosts:
                    host_cache_paths[h["name"]] = os.path.join(
                        cache_dir, f"{_host_cache_key(h)}.wav"
                    )

        cache_hits = [
            h["name"]
            for h in hosts
            if host_cache_paths.get(h["name"])
            and os.path.exists(host_cache_paths[h["name"]])
        ]
        cache_misses = [h["name"] for h in hosts if h["name"] not in cache_hits]

        host_wavs: dict = {}
        if cache_misses:
            if cache_dir:
                progress(
                    0.06,
                    f"Cache: {len(cache_hits)}/{len(hosts)} hit; rendering "
                    f"{len(cache_misses)} host(s)",
                )
            else:
                progress(0.06, "Cache disabled or unavailable; rendering all hosts")
            progress(0.07, "Switching to Deliver page")
            resolve.OpenPage("deliver")
            proj = resolve.GetProjectManager().GetCurrentProject()
            if not proj:
                raise RuntimeError("Lost project handle after switching to Deliver")

            progress(0.08, f"Loading render preset: {settings.render_preset}")
            preset_activated = False
            try:
                if proj.LoadRenderPreset(settings.render_preset):
                    preset_activated = True
            except Exception:
                pass
            if not preset_activated:
                try:
                    if proj.SetCurrentRenderPreset(settings.render_preset):
                        preset_activated = True
                except Exception:
                    pass
            if not preset_activated:
                progress(
                    None,
                    f"WARNING: Could not load render preset '{settings.render_preset}' — using current preset",
                )

            proj.SetCurrentRenderMode(0)
            if not proj.SetRenderSettings({"TargetDir": outdir}):
                raise RuntimeError(f"Could not set render target directory: {outdir}")

            pre_wavs = {f for f in os.listdir(outdir) if f.lower().endswith(".wav")}

            job_id = proj.AddRenderJob()
            if not job_id:
                raise RuntimeError("Could not create render job")

            progress(0.10, "Starting render")
            if not proj.StartRendering():
                try:
                    proj.DeleteRenderJob(job_id)
                except Exception:
                    pass
                raise RuntimeError("StartRendering() returned False")

            while proj.IsRenderingInProgress():
                if cancel():
                    try:
                        proj.StopRendering()
                    except Exception:
                        pass
                    try:
                        proj.DeleteRenderJob(job_id)
                    except Exception:
                        pass
                    return None
                time.sleep(1)

            try:
                proj.DeleteRenderJob(job_id)
            except Exception:
                pass
            progress(0.45, "Render complete")

            new_wavs = [
                os.path.join(outdir, f)
                for f in os.listdir(outdir)
                if f.lower().endswith(".wav") and f not in pre_wavs
            ]
            if not new_wavs:
                raise RuntimeError(f"Render produced no new WAV files in {outdir}")

            rendered_wavs, unmatched, orphans = resolve_render_outputs(hosts, new_wavs)
            if unmatched:
                lines = [f"Could not match rendered WAVs to {len(unmatched)} host(s):"]
                for name in unmatched:
                    lines.append(f"  - {name}")
                if orphans:
                    lines.append(f"Orphan WAVs in {outdir}:")
                    for w in orphans:
                        lines.append(f"  - {os.path.basename(w)}")
                raise RuntimeError("\n".join(lines))

            if cache_dir:
                miss_set = set(cache_misses)
                cached_now = 0
                for h in hosts:
                    if h["name"] not in miss_set:
                        continue
                    src = rendered_wavs.get(h["name"])
                    dst = host_cache_paths.get(h["name"])
                    if not src or not os.path.exists(src) or not dst:
                        continue
                    try:
                        shutil.copy2(src, dst)
                        cached_now += 1
                    except Exception as e:
                        progress(None, f"WARNING: could not cache {h['name']}: {e}")
                if cached_now:
                    progress(None, f"Cache: stored {cached_now} host(s) in {cache_dir}")

            for h in hosts:
                cached = host_cache_paths.get(h["name"])
                if cached and os.path.exists(cached):
                    host_wavs[h["name"]] = cached
                else:
                    host_wavs[h["name"]] = rendered_wavs.get(h["name"])
        else:
            progress(0.45, f"All {len(hosts)} host(s) served from cache; skipping render")
            for h in hosts:
                host_wavs[h["name"]] = host_cache_paths[h["name"]]

        fps = float(proj.GetSetting("timelineFrameRate") or settings.fps_hint)

        host_noise_floors: dict = {}
        host_speech_levels: dict = {}
        host_thresholds_used: dict = {}
        overrides = settings.host_thresholds or {}

        host_jsons: dict = {}
        n = len(hosts)
        for idx, host in enumerate(hosts):
            if cancel():
                return None
            wav = host_wavs.get(host["name"])
            if not wav:
                raise RuntimeError(f"No WAV available for {host['name']}")

            progress(
                0.5 + 0.35 * idx / max(n, 1),
                f"Measuring audio stats for {host['name']}",
            )
            stats = cached_audio_stats(wav)
            floor_db = _percentile_from_stats(stats, 10)
            speech_db = _percentile_from_stats(stats, settings.speech_percentile)
            host_noise_floors[host["name"]] = floor_db
            host_speech_levels[host["name"]] = speech_db

            if host["name"] in overrides:
                threshold_db = float(overrides[host["name"]])
            elif settings.auto_calibrate:
                span = max(0.0, speech_db - floor_db)
                threshold_db = floor_db + max(0.0, min(1.0, settings.strictness)) * span
            else:
                threshold_db = settings.silence_threshold_db
            host_thresholds_used[host["name"]] = threshold_db

            json_path = os.path.join(outdir, f"{host['name']}.json")
            progress(
                0.5 + 0.35 * (idx + 0.5) / max(n, 1),
                f"Detecting silence for {host['name']} @ {threshold_db:.1f} dB "
                f"(floor {floor_db:.1f}, speech {speech_db:.1f})",
            )
            try:
                detect_silence(
                    wav,
                    min_sil_ms=settings.min_silence_ms,
                    pad_ms=settings.padding_ms,
                    out_json=json_path,
                    silence_thresh_db=threshold_db,
                    fps_hint=fps,
                    hold_ms=settings.hold_ms,
                )
            except Exception as e:
                raise RuntimeError(f"Silence detection failed for {host['name']}: {e}")
            if not os.path.exists(json_path):
                raise RuntimeError(f"Silence detection produced no JSON for {host['name']}")

            merged = merge_short_silences_in_json(json_path, settings.min_gated_ms)
            if merged > 0:
                print(
                    f">>> Merged {merged} short silence gap(s) "
                    f"(< {settings.min_gated_ms} ms) for {host['name']}"
                )
            host_jsons[host["name"]] = json_path

        progress(0.92, f"Analysis complete for {n} host(s)")
        return Plan(
            hosts=hosts,
            host_wavs=host_wavs,
            host_jsons=host_jsons,
            fps=fps,
            outdir=outdir,
            cache_hits=len(cache_hits),
            cache_misses=len(cache_misses),
            cache_dir=cache_dir,
            host_noise_floors=host_noise_floors,
            host_thresholds_used=host_thresholds_used,
            host_speech_levels=host_speech_levels,
        )
    finally:
        try:
            resolve.OpenPage("edit")
        except Exception:
            pass


def summarize(plan: Plan) -> list:
    """Per-host stats for a pre-commit preview. Pure; safe to call from a UI thread."""
    summaries: list = []
    for host in plan.hosts:
        json_path = plan.host_jsons.get(host["name"])
        if not json_path or not os.path.exists(json_path):
            continue
        try:
            with open(json_path, "r") as f:
                segs = json.load(f)
        except Exception:
            continue
        n_sil = 0
        n_speech = 0
        gated_ms = 0
        total_ms = 0
        for s in segs:
            start_ms = int(s.get("start_sec", 0) * 1000)
            end_ms = int(s.get("end_sec", 0) * 1000)
            dur = max(0, end_ms - start_ms)
            total_ms += dur
            if s.get("is_silence", False):
                n_sil += 1
                gated_ms += dur
            else:
                n_speech += 1
        pct = (gated_ms / total_ms) if total_ms > 0 else 0.0
        summaries.append(
            SpeakerSummary(
                host_name=host["name"],
                compound_label=host["compound_label"],
                n_segments=len(segs),
                n_silence_segments=n_sil,
                n_speech_segments=n_speech,
                pct_disabled=pct,
                total_gated_ms=gated_ms,
                total_duration_ms=total_ms,
                noise_floor_db=plan.host_noise_floors.get(host["name"]),
                threshold_db=plan.host_thresholds_used.get(host["name"]),
                speech_level_db=plan.host_speech_levels.get(host["name"]),
            )
        )
    return summaries


def commit(
    resolve,
    plan: Plan,
    settings: GateSettings,
    progress: Progress = _default_progress,
    cancel: Cancel = _default_cancel,
) -> Result:
    """Build one ``[Processed]`` audio track per host and disable silence clips."""
    result = Result()
    if cancel():
        result.cancelled = True
        return result

    progress(0.0, f"Building processed tracks for {len(plan.hosts)} host(s)")
    proj, tl, mp = refresh_handles(resolve)

    hosts_sorted = sorted(plan.hosts, key=lambda h: (h["track"], h.get("start_f", 0)))

    label_counts = Counter(h["compound_label"] for h in hosts_sorted)
    for h in hosts_sorted:
        if label_counts[h["compound_label"]] > 1:
            h["compound_name"] = f"{h['compound_label']} A{h['track']}"
        else:
            h["compound_name"] = h["compound_label"]

    current_track_count = tl.GetTrackCount("audio")
    needed_tracks = current_track_count + len(hosts_sorted)
    while tl.GetTrackCount("audio") < needed_tracks:
        tl.AddTrack("audio")
    result.tracks_created = len(hosts_sorted)

    all_infos: list = []
    meta: list = []
    host_segment_counts: list = []

    for host_idx, host in enumerate(hosts_sorted):
        if cancel():
            result.cancelled = True
            return result
        dst_idx = current_track_count + host_idx + 1
        json_path = plan.host_jsons.get(host["name"])
        if not json_path or not os.path.exists(json_path):
            progress(None, f"WARNING: no JSON for {host['name']}")
            host_segment_counts.append(0)
            continue
        segs = load_segments(json_path, plan.fps)
        if not segs:
            progress(None, f"No segments for {host['name']}")
            host_segment_counts.append(0)
            continue

        matching_item = host["item"]
        if not matching_item:
            progress(None, f"WARNING: no original item for {host['name']}")
            host_segment_counts.append(0)
            continue
        mpi = matching_item.GetMediaPoolItem()
        if not mpi:
            progress(None, f"ERROR: no Media Pool item for {host['name']}")
            host_segment_counts.append(0)
            continue

        timeline_start = int(matching_item.GetStart())
        timeline_end = int(matching_item.GetEnd())
        timeline_duration = timeline_end - timeline_start

        segment_count = 0
        for sF, eF, isSil in segs:
            sF = max(0, min(sF, timeline_duration - 1))
            eF = max(0, min(eF, timeline_duration))
            if eF <= sF:
                continue
            record_frame = timeline_start + sF
            info = {
                "mediaPoolItem": mpi,
                "startFrame": sF,
                "endFrame": eF,
                "recordFrame": record_frame,
                "trackIndex": dst_idx,
                "mediaType": 2,
                "trackType": "audio",
            }
            all_infos.append(info)
            meta.append({"is_silence": isSil, "host": host["compound_name"]})
            segment_count += 1
        host_segment_counts.append(segment_count)
        progress(
            0.05 + 0.15 * (host_idx + 1) / max(len(hosts_sorted), 1),
            f"Host {host_idx + 1}/{len(hosts_sorted)} \"{host['compound_name']}\" -> track {dst_idx} ({segment_count} segments)",
        )

    if not all_infos:
        progress(1.0, "No segment clip infos to append")
        return result

    progress(0.2, f"Appending {len(all_infos)} clip(s) in chunks of {settings.batch_size}")
    try:
        added = append_in_chunks(
            all_infos,
            mp,
            resolve=resolve,
            size=settings.batch_size,
            progress=progress,
            cancel=cancel,
            progress_base=0.2,
            progress_span=0.5,
        )
    except Cancelled:
        result.cancelled = True
        return result

    if len(added) != len(all_infos):
        msg = f"expected {len(all_infos)} timeline items, got {len(added)} (per-host grouping may drift)"
        progress(None, f"WARNING: {msg}")
        result.warnings.append(msg)

    proj, tl, mp = refresh_handles(resolve)
    if cancel():
        result.cancelled = True
        return result

    for clip, m in zip(added, meta):
        if not m.get("is_silence", False):
            continue
        method, is_visual = disable_timeline_clip(clip)
        if method is None:
            msg = f"could not disable silence clip on \"{m['host']}\" (all methods failed)"
            progress(None, f"WARNING: {msg}")
            result.warnings.append(msg)
            continue
        result.disabled_count += 1
        result.disabled_by_method[method] = result.disabled_by_method.get(method, 0) + 1
        if not is_visual:
            msg = (
                f"disabled by {method} fallback on \"{m['host']}\" — clip is silent but NOT "
                f"greyed out; visual review is broken for this clip"
            )
            progress(None, f"WARNING: {msg}")
            result.warnings.append(msg)

    progress(0.85, f"Disabled {result.disabled_count} silence clip(s)")

    for host_idx, host in enumerate(hosts_sorted):
        dst_idx = current_track_count + host_idx + 1
        try:
            tl.SetTrackName("audio", dst_idx, f"[Processed] {host['compound_name']}")
        except Exception:
            pass

    n_with_segments = sum(1 for n in host_segment_counts if n > 0)
    progress(
        1.0,
        f"Done: {n_with_segments} speaker track(s) on audio tracks "
        f"{current_track_count + 1}-{current_track_count + len(hosts_sorted)}",
    )
    return result


def run_headless(
    resolve,
    settings: Optional[GateSettings] = None,
    progress: Optional[Progress] = None,
    cancel: Optional[Cancel] = None,
) -> Optional[Result]:
    """Convenience driver: analyze -> commit.

    OUTDIR (containing WAVs + JSONs) is cleaned up on success. On cancel or
    failure it is left in place so the run is debuggable.
    """
    settings = settings or GateSettings()
    progress = progress or _default_progress
    cancel = cancel or _default_cancel

    plan: Optional[Plan] = None
    try:
        plan = analyze(resolve, settings, progress, cancel)
        if plan is None:
            progress(None, "Cancelled during analyze")
            return None
        result = commit(resolve, plan, settings, progress, cancel)
    except Cancelled:
        if plan is not None:
            progress(None, f"Cancelled — analysis artifacts kept in: {plan.outdir}")
        return None
    except Exception:
        if plan is not None:
            progress(None, f"Failure — analysis artifacts kept in: {plan.outdir}")
        raise

    if result.cancelled:
        progress(None, f"Cancelled during commit — analysis artifacts kept in: {plan.outdir}")
        return result

    try:
        shutil.rmtree(plan.outdir, ignore_errors=True)
    except Exception:
        pass
    return result
