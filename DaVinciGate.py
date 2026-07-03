#!/usr/bin/env python3
"""
DaVinci Gate — entry point.

Runs the audio "checkerboarding" pipeline: for each host clip on the timeline,
render its audio, detect silence, and rebuild that speaker onto a new
``[Processed] …`` audio track with silence clips greyed out (SetClipEnabled).

The pipeline itself lives in :mod:`gate_core` so a future in-Resolve UI can
drive the same ``analyze``/``commit`` split as this headless entry does.

Run from Workspace -> Scripts -> Utility -> DaVinciGate.
"""

import os
import sys
from typing import Optional


try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
except NameError:
    script_dir = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv else os.getcwd()

_module_search_paths = [
    script_dir,
    os.path.join(script_dir, "Utility"),
    os.path.expanduser(
        "~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
    ),
    os.getcwd(),
]

for _p in _module_search_paths:
    if os.path.exists(os.path.join(_p, "gate_core.py")):
        if _p not in sys.path:
            sys.path.insert(0, _p)
        break
else:
    print("ERROR: Could not find gate_core.py. Searched:")
    for _p in _module_search_paths:
        print(f"  - {_p}")
    sys.exit(1)

try:
    from gate_core import (
        Cancelled,
        GateSettings,
        analyze,
        clear_project_cache,
        commit,
        discover_hosts,
        run_headless,
        summarize,
    )
except ImportError as e:
    print(f"ERROR: Could not import gate_core: {e}")
    sys.exit(1)


_api_candidates = []
if os.environ.get("RESOLVE_SCRIPT_API"):
    _api_candidates.append(os.path.join(os.environ["RESOLVE_SCRIPT_API"], "Modules"))

if sys.platform == "darwin":
    _api_candidates.extend(
        [
            "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Resources/Developer/Scripting/Modules",
            "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
            os.path.expanduser(
                "~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
            ),
        ]
    )
elif sys.platform == "win32":
    _api_candidates.extend(
        [
            os.path.expanduser(
                "~/AppData/Roaming/Blackmagic Design/DaVinci Resolve/Support/Developer/Scripting/Modules"
            ),
            "C:/Program Files/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
            "C:/Program Files (x86)/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
        ]
    )
elif sys.platform.startswith("linux"):
    _api_candidates.extend(
        [
            os.path.expanduser("~/.local/share/DaVinciResolve/Developer/Scripting/Modules"),
            "/opt/resolve/Developer/Scripting/Modules",
            "/usr/local/DaVinciResolve/Developer/Scripting/Modules",
        ]
    )

for _p in _api_candidates:
    if _p and os.path.isdir(_p) and _p not in sys.path:
        sys.path.append(_p)

try:
    import DaVinciResolveScript as _dvr
    resolve = _dvr.scriptapp("Resolve")
except Exception as e:
    print(f"ERROR: DaVinci Resolve API not available: {e}")
    sys.exit(1)


_CONFIG_MAPPING = {
    "SILENCE_THRESHOLD_DB": "silence_threshold_db",
    "MIN_SILENCE_MS": "min_silence_ms",
    "PADDING_MS": "padding_ms",
    "HOLD_MS": "hold_ms",
    "BATCH_SIZE": "batch_size",
    "RENDER_PRESET": "render_preset",
    "FPS_HINT": "fps_hint",
    "TEMP_DIR": "temp_dir",
}


def _build_settings() -> GateSettings:
    """Populate GateSettings from an optional ``config.py`` beside this script.

    Missing config is fine — the dataclass defaults reproduce v4's behavior.
    """
    settings = GateSettings()
    try:
        import config
    except ImportError:
        return settings
    for old, new in _CONFIG_MAPPING.items():
        if hasattr(config, old):
            setattr(settings, new, getattr(config, old))
    return settings


def _print_result(result) -> None:
    if result is None:
        print(">>> Cancelled.")
        return
    if result.warnings:
        print(f">>> {len(result.warnings)} warning(s) during run:")
        for w in result.warnings:
            print(f"    - {w}")
    if result.disabled_by_method:
        print(f">>> Disable methods used: {result.disabled_by_method}")
    print(
        f">>> Processing complete: {result.disabled_count} silence clip(s) disabled "
        f"across {result.tracks_created} processed track(s)."
    )


def _try_discover_hosts(resolve) -> list:
    """Best-effort host discovery for the UI's summary label.

    Never raises: any failure (no project, no timeline, no clips) returns [].
    """
    try:
        proj = resolve.GetProjectManager().GetCurrentProject()
        if not proj:
            return []
        tl = proj.GetCurrentTimeline()
        if not tl:
            return []
        return discover_hosts(tl, GateSettings())
    except Exception:
        return []


def _hosts_summary(hosts: list) -> str:
    if not hosts:
        return (
            "No hosts detected. Open a timeline with named audio clips, "
            "then click Refresh."
        )
    parts = [f"Track {h['track']}: \"{h['compound_label']}\"" for h in hosts]
    return f"Hosts ({len(hosts)}): " + ", ".join(parts)


def _fmt_ms_hms(ms: int) -> str:
    """Format milliseconds as H:MM:SS (or M:SS if under an hour)."""
    if ms is None:
        return "-"
    secs = int(max(0, ms) // 1000)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _launch_ui(resolve, initial_settings: GateSettings) -> bool:
    """Open the UIManager window: Analyze/Apply flow with adaptive
    auto-calibration.

    Flow:
      1. On open, discover hosts. Per-host override rows are baked into the
         layout for the current host list.
      2. User clicks Analyze -> renders (or reuses WAV cache), measures
         audio stats per host, computes an adaptive threshold from
         (noise floor + strictness * (speech level - noise floor)) with
         per-host overrides taking precedence. Then runs silence detection
         and shows the per-host preview table. The window is blocked during
         the render — progress text streams to the Resolve Console.
      3. User inspects, tweaks any setting or per-host override, clicks
         Analyze again (fast: cache hits + cached stats), Apply to commit.

    Returns True on clean open+close, False if UI init failed (fall back to
    headless).
    """
    try:
        fusion = resolve.Fusion()
    except Exception as e:
        print(f">>> UI init: could not access Fusion(): {e}")
        return False
    if not fusion:
        print(">>> UI init: Fusion() returned None")
        return False

    try:
        ui = fusion.UIManager
        disp = _dvr.UIDispatcher(ui)
    except Exception as e:
        print(f">>> UI init: UIManager/UIDispatcher not available: {e}")
        return False

    initial_hosts = _try_discover_hosts(resolve)
    LABEL_MIN = [240, 0]

    def _field_row(label_text: str, field_id: str, initial: str):
        return ui.HGroup(
            {"Weight": 0, "Spacing": 8},
            [
                ui.Label({"Text": label_text, "Weight": 0, "MinimumSize": LABEL_MIN}),
                ui.LineEdit({"ID": field_id, "Text": initial, "Weight": 1.0}),
            ],
        )

    def _override_row(host):
        indent = "    "
        label = f"{indent}{host['compound_label']} (track {host['track']}):"
        return ui.HGroup(
            {"Weight": 0, "Spacing": 8},
            [
                ui.Label({"Text": label, "Weight": 0, "MinimumSize": LABEL_MIN}),
                ui.LineEdit(
                    {
                        "ID": f"HostOverride_{host['name']}",
                        "Text": "",
                        "PlaceholderText": "auto",
                        "Weight": 1.0,
                    }
                ),
            ],
        )

    override_rows = [_override_row(h) for h in initial_hosts]
    override_header_text = (
        "Per-host threshold overrides (dB, blank = auto):"
        if initial_hosts
        else "Per-host threshold overrides: (no hosts detected — reopen after loading a timeline)"
    )
    strictness_initial = f"{initial_settings.strictness * 100.0:.0f}"

    try:
        win = disp.AddWindow(
            {
                "ID": "DGateMainWindow",
                "WindowTitle": "DaVinci Gate",
                "Geometry": [180, 180, 820, 720],
            },
            [
                ui.VGroup(
                    {"Spacing": 8},
                    [
                        ui.Label(
                            {
                                "ID": "TitleLabel",
                                "Text": "DaVinci Gate",
                                "Weight": 0,
                            }
                        ),
                        ui.Label(
                            {
                                "ID": "HostsLabel",
                                "Text": _hosts_summary(initial_hosts),
                                "Weight": 0,
                                "WordWrap": True,
                            }
                        ),
                        ui.CheckBox(
                            {
                                "ID": "AutoCalibrateCheckBox",
                                "Text": "Auto-calibrate threshold per host (recommended)",
                                "Checked": initial_settings.auto_calibrate,
                                "Weight": 0,
                            }
                        ),
                        _field_row(
                            "Strictness (%, higher = more strict):",
                            "StrictnessField",
                            strictness_initial,
                        ),
                        _field_row(
                            "Manual threshold (dB, if auto off):",
                            "ThresholdField",
                            f"{initial_settings.silence_threshold_db}",
                        ),
                        _field_row(
                            "Min silence (ms, detection):",
                            "MinSilenceField",
                            f"{initial_settings.min_silence_ms}",
                        ),
                        _field_row(
                            "Min silence to gate (ms, timeline):",
                            "MinGatedField",
                            f"{initial_settings.min_gated_ms}",
                        ),
                        _field_row(
                            "Padding (ms):",
                            "PaddingField",
                            f"{initial_settings.padding_ms}",
                        ),
                        ui.Label(
                            {
                                "ID": "OverrideHeader",
                                "Text": override_header_text,
                                "Weight": 0,
                            }
                        ),
                        *override_rows,
                        ui.Label(
                            {
                                "ID": "PreviewLabel",
                                "Text": "Per-host preview (empty; click Analyze):",
                                "Weight": 0,
                            }
                        ),
                        ui.Tree(
                            {
                                "ID": "PreviewTree",
                                "Weight": 1.0,
                                "MinimumSize": [640, 120],
                                "AlternatingRowColors": True,
                                "RootIsDecorated": False,
                                "ItemsExpandable": False,
                            }
                        ),
                        ui.Label(
                            {
                                "ID": "StatusLabel",
                                "Text": "Ready. Click Analyze to render, cache, and preview.",
                                "Weight": 0,
                                "WordWrap": True,
                            }
                        ),
                        ui.HGroup(
                            {"Weight": 0, "Spacing": 8},
                            [
                                ui.Button({"ID": "RefreshButton", "Text": "Refresh hosts"}),
                                ui.Button({"ID": "AnalyzeButton", "Text": "Analyze"}),
                                ui.Button({"ID": "ApplyButton", "Text": "Apply", "Enabled": False}),
                                ui.Button({"ID": "ClearCacheButton", "Text": "Clear cache"}),
                                ui.Button({"ID": "CloseButton", "Text": "Close"}),
                            ],
                        ),
                    ],
                )
            ],
        )
    except Exception as e:
        print(f">>> UI init: AddWindow failed: {e}")
        return False
    if win is None:
        print(">>> UI init: AddWindow returned None")
        return False

    try:
        items = win.GetItems()
    except Exception:
        items = {}

    try:
        tree = items["PreviewTree"]
        tree.ColumnCount = 6
        tree.SetHeaderLabels(
            [
                "Host",
                "Floor (dB)",
                "Speech (dB)",
                "Threshold (dB)",
                "Silence %",
                "Gated time",
            ]
        )
    except Exception as e:
        print(f">>> UI init: Tree setup failed ({e}); preview table may render oddly")

    state = {
        "plan": None,
        "analyzed_settings": None,
    }

    def _set_text(field_id: str, text: str) -> None:
        try:
            items[field_id].Text = text
        except Exception:
            pass

    def _set_enabled(field_id: str, enabled: bool) -> None:
        try:
            items[field_id].Enabled = enabled
        except Exception:
            pass

    def _set_status(msg: str) -> None:
        _set_text("StatusLabel", msg)

    def _busy(is_busy: bool) -> None:
        for bid in ("RefreshButton", "AnalyzeButton", "ApplyButton", "ClearCacheButton"):
            _set_enabled(bid, not is_busy)
        if not is_busy:
            _set_enabled("ApplyButton", state["plan"] is not None)

    def _invalidate_plan(reason: Optional[str] = None) -> None:
        if state["plan"] is not None or reason:
            state["plan"] = None
            state["analyzed_settings"] = None
            _set_enabled("ApplyButton", False)
            if reason:
                _set_status(reason)

    def _clear_tree() -> None:
        try:
            items["PreviewTree"].Clear()
        except Exception:
            pass

    def _populate_tree(summaries) -> None:
        _clear_tree()
        tree_w = items.get("PreviewTree")
        if tree_w is None:
            return
        for s in summaries:
            try:
                row = tree_w.NewItem()
                row.Text[0] = s.host_name
                row.Text[1] = f"{s.noise_floor_db:.1f}" if s.noise_floor_db is not None else "-"
                row.Text[2] = f"{s.speech_level_db:.1f}" if s.speech_level_db is not None else "-"
                row.Text[3] = f"{s.threshold_db:.1f}" if s.threshold_db is not None else "-"
                row.Text[4] = f"{s.pct_disabled * 100.0:.1f}%"
                row.Text[5] = _fmt_ms_hms(s.total_gated_ms)
                tree_w.AddTopLevelItem(row)
            except Exception:
                continue

    def _read_settings():
        try:
            auto_cal = bool(items["AutoCalibrateCheckBox"].Checked)
        except Exception:
            auto_cal = True
        try:
            strictness_pct = float(items["StrictnessField"].Text)
            threshold = float(items["ThresholdField"].Text)
            min_sil = int(items["MinSilenceField"].Text)
            min_gated = int(items["MinGatedField"].Text)
            padding = int(items["PaddingField"].Text)
        except (KeyError, ValueError) as e:
            return None, f"Invalid input in settings: {e}"
        if min_sil < 0 or min_gated < 0 or padding < 0:
            return None, (
                "Min silence / min silence to gate / padding must be non-negative."
            )
        if strictness_pct < 0 or strictness_pct > 100:
            return None, "Strictness must be between 0 and 100."

        host_overrides: dict = {}
        for host in initial_hosts:
            fid = f"HostOverride_{host['name']}"
            raw = ""
            try:
                raw = items[fid].Text.strip()
            except Exception:
                pass
            if not raw:
                continue
            try:
                host_overrides[host["name"]] = float(raw)
            except ValueError:
                return None, (
                    f"Invalid per-host override for "
                    f"'{host['compound_label']}' (track {host['track']}): {raw!r}"
                )

        new = GateSettings(
            silence_threshold_db=threshold,
            min_silence_ms=min_sil,
            padding_ms=padding,
            hold_ms=initial_settings.hold_ms,
            min_gated_ms=min_gated,
            batch_size=initial_settings.batch_size,
            render_preset=initial_settings.render_preset,
            fps_hint=initial_settings.fps_hint,
            track_name_normalize=initial_settings.track_name_normalize,
            temp_dir=initial_settings.temp_dir,
            selected_tracks=initial_settings.selected_tracks,
            use_cache=initial_settings.use_cache,
            cache_dir=initial_settings.cache_dir,
            auto_calibrate=auto_cal,
            strictness=strictness_pct / 100.0,
            speech_percentile=initial_settings.speech_percentile,
            host_thresholds=host_overrides or None,
        )
        return new, None

    def on_close(event=None):
        disp.ExitLoop()

    def on_settings_change(event=None):
        _invalidate_plan("Settings changed — click Analyze to refresh preview.")

    def on_refresh(event=None):
        hosts = _try_discover_hosts(resolve)
        _set_text("HostsLabel", _hosts_summary(hosts))
        _clear_tree()
        if not hosts:
            _invalidate_plan("No hosts on current timeline.")
            return
        initial_names = {h["name"] for h in initial_hosts}
        current_names = {h["name"] for h in hosts}
        if initial_names != current_names:
            _invalidate_plan(
                f"Refreshed. {len(hosts)} host(s) — but the host set differs "
                "from what this window was opened with. Per-host overrides "
                "map by name, so any renamed/added hosts won't have override "
                "rows until you close and reopen this window."
            )
        else:
            _invalidate_plan(
                f"Refreshed. {len(hosts)} host(s) detected — click Analyze."
            )

    def on_analyze(event=None):
        new_settings, err = _read_settings()
        if err:
            _set_status(err)
            return
        _busy(True)
        _set_status(
            "Analyzing… rendering / measuring / detecting. See Console for progress."
        )
        try:
            plan = analyze(resolve, new_settings)
        except Cancelled:
            _set_status("Analyze cancelled.")
            _invalidate_plan()
            _busy(False)
            return
        except Exception as e:
            _set_status(f"ERROR during Analyze: {e}")
            _invalidate_plan()
            _busy(False)
            return
        if plan is None:
            _set_status("Analyze produced no plan (cancelled or no hosts).")
            _invalidate_plan()
            _busy(False)
            return

        state["plan"] = plan
        state["analyzed_settings"] = new_settings
        try:
            summaries = summarize(plan)
        except Exception as e:
            summaries = []
            print(f">>> WARNING: summarize failed: {e}")
        _populate_tree(summaries)

        total_gated = sum(s.total_gated_ms for s in summaries)
        total_sil = sum(s.n_silence_segments for s in summaries)
        cache_note = ""
        if plan.cache_misses == 0 and plan.cache_hits > 0:
            cache_note = " (all hosts from cache)"
        elif plan.cache_misses > 0:
            cache_note = f" (rendered {plan.cache_misses}, cached {plan.cache_hits})"

        _set_status(
            f"Analyzed{cache_note}. {total_sil} silence segment(s) total, "
            f"{_fmt_ms_hms(total_gated)} would be gated. Click Apply to commit."
        )
        _busy(False)

    def on_apply(event=None):
        if state["plan"] is None or state["analyzed_settings"] is None:
            _set_status("Nothing to apply — click Analyze first.")
            return
        _busy(True)
        _set_status("Applying to timeline… see Console for progress.")
        try:
            result = commit(resolve, state["plan"], state["analyzed_settings"])
        except Cancelled:
            _set_status("Apply cancelled.")
            _busy(False)
            return
        except Exception as e:
            _set_status(f"ERROR during Apply: {e}")
            _busy(False)
            return
        _print_result(result)
        _invalidate_plan()
        if result is None:
            _set_status("Applied: cancelled.")
        else:
            method_str = (
                ", ".join(f"{k}={v}" for k, v in (result.disabled_by_method or {}).items())
                or "n/a"
            )
            warn_count = len(result.warnings) if result.warnings else 0
            _set_status(
                f"Applied. Disabled {result.disabled_count} clip(s) across "
                f"{result.tracks_created} track(s). Methods: {method_str}. "
                f"Warnings: {warn_count}. Re-Analyze to try different settings."
            )
        _busy(False)

    def on_clear_cache(event=None):
        cleared = clear_project_cache(resolve, initial_settings.cache_dir)
        if cleared:
            _set_status(f"Cleared cache for current project: {cleared}")
        else:
            _set_status("No cache to clear (or cache path unavailable).")
        _invalidate_plan()

    win.On.DGateMainWindow.Close = on_close
    win.On.CloseButton.Clicked = on_close
    win.On.RefreshButton.Clicked = on_refresh
    win.On.AnalyzeButton.Clicked = on_analyze
    win.On.ApplyButton.Clicked = on_apply
    win.On.ClearCacheButton.Clicked = on_clear_cache
    win.On.AutoCalibrateCheckBox.Clicked = on_settings_change

    field_ids = [
        "StrictnessField",
        "ThresholdField",
        "MinSilenceField",
        "MinGatedField",
        "PaddingField",
    ]
    for host in initial_hosts:
        field_ids.append(f"HostOverride_{host['name']}")
    for fid in field_ids:
        try:
            getattr(win.On, fid).TextEdited = on_settings_change
        except Exception:
            try:
                getattr(win.On, fid).TextChanged = on_settings_change
            except Exception:
                pass

    win.Show()
    disp.RunLoop()
    win.Hide()
    return True


def main() -> int:
    if not resolve:
        print("ERROR: Could not connect to DaVinci Resolve")
        return 1

    settings = _build_settings()

    if os.environ.get("DAVINCIGATE_HEADLESS"):
        print(">>> Headless mode (DAVINCIGATE_HEADLESS is set)")
        result = run_headless(resolve, settings)
        _print_result(result)
        return 0 if result is not None else 130

    if _launch_ui(resolve, settings):
        return 0

    print(">>> UI unavailable; falling back to headless mode.")
    result = run_headless(resolve, settings)
    _print_result(result)
    return 0 if result is not None else 130


if __name__ == "__main__":
    sys.exit(main())
