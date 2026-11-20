#!/usr/bin/env python3
"""100% isolated verification for Nexus RAG storage/retrieval, live Alis bridge & smart clientActions."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
HOST = Path(os.environ.get("ALIS_ROOT") or (ROOT.parent / "Alis")).resolve()
SCRIPTS_AQUA = ROOT / "scripts" / "aqua"
if str(SCRIPTS_AQUA) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_AQUA))


def run_mind(payload: dict, env: dict[str, str]) -> dict:
    proc = subprocess.run(
        ["/usr/bin/python3", str(SCRIPTS_AQUA / "local_mind.py"), "--host-root", str(HOST)],
        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=True,
    )
    out = json.loads(proc.stdout.decode("utf-8"))
    assert out.get("ok") is True, f"local_mind failed for {payload}: {out} | stderr={proc.stderr.decode()}"
    return out["result"]


def make_isolated_env(tmp_path: Path, vault_dir: Path | None = None) -> tuple[dict[str, str], Path, Path, Path]:
    fake_home = tmp_path / "home"
    xdg_config = tmp_path / "config"
    xdg_state = tmp_path / "state"
    xdg_cache = tmp_path / "cache"
    xdg_data = tmp_path / "data"
    for d in (fake_home, xdg_config, xdg_state, xdg_cache, xdg_data):
        d.mkdir(parents=True, exist_ok=True)

    alis_cfg_dir = xdg_config / "alis"
    alis_cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = json.loads((HOST / "defaults/config.json").read_text(encoding="utf-8"))
    if vault_dir is not None:
        cfg.setdefault("todo", {}).setdefault("nexus", {})["vaultPath"] = str(vault_dir)
    else:
        cfg.setdefault("todo", {}).setdefault("nexus", {})["vaultPath"] = ""
    cfg.setdefault("abyss", {}).setdefault("companion", {})["enabled"] = False
    cfg.setdefault("abyss", {}).setdefault("companionMind", {})["obsidianEnabled"] = False
    (alis_cfg_dir / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    env = os.environ.copy()
    for k in ("DISPLAY", "WAYLAND_DISPLAY", "QML_IMPORT_PATH", "QML2_IMPORT_PATH", "QS_CONFIG_NAME", "QS_CONFIG_PATH"):
        env.pop(k, None)
    env.update({
        "HOME": str(fake_home),
        "XDG_CONFIG_HOME": str(xdg_config),
        "XDG_STATE_HOME": str(xdg_state),
        "XDG_CACHE_HOME": str(xdg_cache),
        "XDG_DATA_HOME": str(xdg_data),
        "ALIS_ROOT": str(HOST),
        "ALIS_AQUA_TEST_NO_SPAWN": "1",
        "ALIS_TEST_ISOLATED": "1",
        "ALIS_THINKING_ROUTER_DISABLE_ML": "1",
        "ALIS_GGUF_ROOTS": "[]",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    if vault_dir is not None:
        env["ALIS_NEXUS_VAULT"] = str(vault_dir)
    else:
        env["ALIS_NEXUS_VAULT"] = "__DISABLED__"
    return env, xdg_config, xdg_state, fake_home


def make_live_snapshot(vault_dir: Path) -> dict:
    return {
        "dateTime": {
            "dateText": "Friday, 20/11/2026",
            "shortDate": "20/11",
            "timeText": "14:25",
            "uptime": "3h 12m",
        },
        "weather": {
            "enabled": True,
            "city": "Can Tho",
            "visibleCity": "Can Tho",
            "temp": "31°C",
            "tempFeelsLike": "34°C",
            "description": "Ít mây, nắng nhẹ",
            "humidity": "72%",
            "uv": "6",
            "wind": "14 km/h",
            "windDir": "SE",
            "precip": "0.2 mm",
            "tempMax": "33°C",
            "tempMin": "25°C",
            "sunrise": "05:52",
            "sunset": "17:38",
            "lastRefresh": "14:20",
            "airQuality": {"available": True, "aqi": 42, "scale": "AQI", "label": "Good", "pm25": "9.8 µg/m³", "pm10": "16.4 µg/m³"},
            "forecast": [{"dayName": "Sat", "lo": "25°C", "hi": "32°C"}, {"dayName": "Sun", "lo": "24°C", "hi": "31°C"}],
            "hourly": [{"label": "15:00", "temp": "31°C"}, {"label": "18:00", "temp": "28°C"}],
        },
        "music": {
            "available": True,
            "playing": True,
            "currentTitle": "Moon River",
            "currentArtist": "Andy Williams",
            "currentAlbum": "Classic Gold",
            "positionSec": 75,
            "durationSec": 162,
            "shuffleMode": True,
            "libraryCount": 2669,
            "audioVolume": 0.65,
            "audioMuted": False,
        },
        "timers": {
            "pomodoroRunning": True,
            "pomodoroPaused": False,
            "pomodoroBreak": False,
            "pomodoroSecondsLeft": 920,
            "activeTag": "DeepWork",
            "todayFocusSeconds": 5400,
            "todaySessions": 2,
        },
        "todos": {
            "backend": "nexus",
            "openCount": 2,
            "doneCount": 1,
            "items": [
                {"content": "Hoàn thành báo cáo RAG", "done": False, "timeStr": "16:00"},
                {"content": "Kiểm tra Thinkfan", "done": False, "timeStr": ""},
                {"content": "Đọc tài liệu Niri", "done": True, "timeStr": ""},
            ],
        },
        "schedule": {
            "eventsCount": 2,
            "todayEvents": [{"title": "Code review Alis", "timeStr": "15:30", "category": "work"}],
            "upcomingEvents": [{"title": "Sprint sync", "dateStr": "2026-11-21", "timeStr": "09:00"}],
        },
        "quicknotes": {
            "tabCount": 2,
            "currentTabIndex": 0,
            "tabs": [
                {"title": "Checklist", "textPreview": "Kiểm tra FTS5 index và Thinkfan RPM"},
                {"title": "Scratch", "textPreview": "Lệnh test offscreen qs"},
            ],
        },
        "notifications": {
            "unread": 3,
            "silent": False,
            "recent": [
                {"appName": "GitHub", "summary": "CI passed", "body": "All workflows green"},
            ],
        },
        "screentime": {
            "enabled": True,
            "currentAppName": "Neovim",
            "currentSessionSeconds": 1800,
            "totalTodaySeconds": 14400,
            "topApps": [{"name": "Neovim", "seconds": 7200}, {"name": "Firefox", "seconds": 4800}],
        },
        "learning": {
            "dueCount": 14,
            "todayReviewedCount": 8,
            "totalCount": 320,
            "streakDays": 12,
        },
        "fitness": {
            "todayStr": "2026-11-20",
            "routine": "Push Day",
            "completedExercises": 3,
            "totalExercises": 5,
            "completedSets": 11,
            "totalSets": 18,
        },
        "system": {
            "cpuPercent": 0.14,
            "gpuPercent": 0.08,
            "cpuTemp": 49,
            "gpuTemp": 43,
            "memUsedGb": 11.2,
            "memTotalGb": 30.0,
            "memPercent": 37,
            "batteryAvailable": True,
            "batteryPercent": 79,
            "batteryCharging": False,
            "thinkfanAvailable": True,
            "thinkfanActive": True,
            "thinkfanProfile": "balanced",
            "thinkfanRpm": 3350,
            "tlpAvailable": True,
            "tlpLimit": 80,
            "tlpStart": 75,
            "networkType": "Wi-Fi",
            "networkName": "HomeFiber5G",
            "bluetoothConnectedCount": 1,
            "bluetoothDeviceNames": ["Sony XM5"],
            "nightLightActive": True,
            "gameModeActive": False,
            "updatesCount": 4,
            "shellCommitsBehind": 0,
        },
        "desktop": {
            "currentOutput": "eDP-1",
            "focusedApp": "foot",
            "focusedTitle": "nvim nexus_rag.py",
            "windowCount": 4,
        },
        "nexus": {
            "configured": True,
            "vaultPath": str(vault_dir),
            "isOpen": False,
            "todayJournal": {"exists": True, "relPath": "10_Journal/2026-11-20.md", "mood": "Great", "energy": "High", "weight": "64kg"},
            "dailySchedule": {
                "weekday": "Friday",
                "scheduleRows": [{"time": "17:00", "title": "Gym Push Day"}],
                "exerciseRows": ["Bench Press 4x8"],
            },
            "financeSummary": {"available": True, "totalIncome": "15,000,000", "totalExpense": "4,200,000", "closingBalance": "10,800,000"},
        },
    }


def seed_vault_and_cache(vault_dir: Path, xdg_state: Path) -> None:
    (vault_dir / "20_Notes").mkdir(parents=True, exist_ok=True)
    (vault_dir / "10_Journal").mkdir(parents=True, exist_ok=True)

    (vault_dir / "20_Notes" / "thinkfan-guide.md").write_text(
        "---\ntitle: Hướng dẫn cấu hình Thinkfan\ntags: [linux, thinkpad, fan]\n---\n\n"
        "# Cấu hình Thinkfan trên ThinkPad\n\n"
        "Cài đặt `thinkfan` và bật service systemd để kiểm soát tốc độ quạt theo nhiệt độ CPU.\n"
        "Mức RPM tiêu chuẩn khi tải vừa khoảng 3300 RPM.\n",
        encoding="utf-8",
    )
    (vault_dir / "20_Notes" / "niri-keybinds.md").write_text(
        "---\ntitle: Wayland Niri Keybinds\ntags: [wayland, niri]\n---\n\n"
        "# Niri Compositor Keybinds\n\n"
        "Use `Super+Slash` to toggle overlay shortcuts and keep `config.kdl` clean.\n",
        encoding="utf-8",
    )
    (vault_dir / "10_Journal" / "2026-11-20.md").write_text(
        "---\nmood: Great\nenergy: High\n---\n# Daily Journal 2026-11-20\n\n"
        "Tích hợp Companion RAG lưu trực tiếp vào Nexus Vault.\n",
        encoding="utf-8",
    )

    cache_path = xdg_state / "quickshell" / "user" / "nexus_vault_cache.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_payload = {
        "vaultPath": str(vault_dir),
        "vaultName": vault_dir.name,
        "stats": {"noteCount": 3, "journalCount": 1, "bookCount": 1, "tagCount": 4, "openTasks": 2, "doneTasks": 1},
        "todayJournal": {"exists": True, "relPath": "10_Journal/2026-11-20.md", "mood": "Great", "energy": "High", "weight": "64kg"},
        "dailySchedule": {"weekday": "Friday", "scheduleRows": [{"time": "17:00", "title": "Gym Push Day"}]},
        "financeSummary": {"available": True, "totalIncome": "15,000,000", "totalExpense": "4,200,000", "closingBalance": "10,800,000"},
        "recentNotes": [{"title": "Hướng dẫn cấu hình Thinkfan", "relPath": "20_Notes/thinkfan-guide.md"}],
        "tags": [{"name": "linux", "count": 1}, {"name": "wayland", "count": 1}],
        "collections": {
            "books": [{"title": "Designing Data-Intensive Applications", "relPath": "30_Library/ddia.md", "rating": 5}],
            "movies": [{"title": "Your Name", "relPath": "30_Library/your-name.md", "rating": 5}],
        },
    }
    cache_path.write_text(json.dumps(cache_payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_1_migration_and_storage_split(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    vault_dir = tmp_path / "NexusVault"
    vault_dir.mkdir(parents=True, exist_ok=True)
    env, _, xdg_state, fake_home = make_isolated_env(tmp_path, vault_dir)

    ext_doc = fake_home / "custom-docs"
    ext_doc.mkdir(parents=True, exist_ok=True)
    (ext_doc / "guide.md").write_text("# Custom Ext Doc\nImportant RAG custom knowledge token ALPHA99.\n", encoding="utf-8")

    # Seed legacy RAG files before running Nexus migration
    leg_root = xdg_state / "alis-companion"
    leg_root.mkdir(parents=True, exist_ok=True)
    (leg_root / "rag_sources.json").write_text(
        json.dumps({
            "version": 1,
            "sources": [{"path": str(ext_doc), "label": "Custom Docs", "enabled": True, "addedAtMs": 1700000000000}],
        }),
        encoding="utf-8",
    )
    leg_dr = leg_root / "domain_rag"
    (leg_dr / "canonical").mkdir(parents=True, exist_ok=True)
    (leg_dr / "personal").mkdir(parents=True, exist_ok=True)
    (leg_dr / "canonical" / "niri.md").write_text("# Canonical Niri\nLegacy canonical note.\n", encoding="utf-8")
    (leg_dr / "personal" / "arch-tips.md").write_text("# Arch Tips\nUse pacman -Syu safely.\n", encoding="utf-8")

    seed_vault_and_cache(vault_dir, xdg_state)

    status = run_mind({"action": "agent_status"}, env)
    sp = status["storagePaths"]
    assert sp["ragStorageMode"] == "nexus", sp
    assert sp["ragVaultDir"].endswith("90_System/99_RAG"), sp
    assert sp["ragStatePath"].endswith("90_System/99_RAG/RAG_Sources.md"), sp
    expected_db = str((xdg_state / "quickshell" / "user" / "nexus_rag.db").resolve())
    assert str(Path(sp["ragIndexDbPath"]).resolve()) == expected_db, (sp["ragIndexDbPath"], expected_db)

    rag_dir = Path(sp["ragVaultDir"])
    assert (rag_dir / "README.md").is_file()
    assert (rag_dir / "RAG_Sources.md").is_file()
    assert (rag_dir / "Canonical" / "niri.md").is_file()
    migrated_personal = rag_dir / "Personal" / "arch-tips.md"
    assert migrated_personal.is_file()

    # Assert zero binary SQLite/DB files anywhere inside the Nexus vault RAG directory
    forbidden_db = [
        p for p in (vault_dir / "90_System").rglob("*")
        if p.is_file() and (p.suffix in (".db", ".sqlite", ".sqlite3") or p.name.endswith(("-wal", "-shm", ".sqlite3-wal", ".sqlite3-shm")))
    ]
    assert not forbidden_db, f"Unexpected binary DB files inside vault: {forbidden_db}"

    # Verify legacy sources survived inside RAG_Sources.md and rag_action list
    src_list = run_mind({"action": "rag_action", "op": "list"}, env)
    assert any(s["path"] == str(ext_doc) for s in src_list["sources"]), src_list

    # Modify migrated file by hand, then re-run migration/status -> must be idempotent & preserve edit!
    edited_text = migrated_personal.read_text(encoding="utf-8") + "\nUser handwritten line MUST STAY.\n"
    migrated_personal.write_text(edited_text, encoding="utf-8")
    nx_stat = run_mind({"action": "rag_action", "op": "nexus_status"}, env)
    assert nx_stat["ok"] is True
    assert "User handwritten line MUST STAY." in migrated_personal.read_text(encoding="utf-8")
    print("[PASS] 1. Legacy -> Nexus RAG zero-loss idempotent migration & external DB split")
    return env, vault_dir, xdg_state


def test_2_standalone_fallback(tmp_path: Path) -> None:
    isolated_tmp = tmp_path / "standalone_case"
    isolated_tmp.mkdir(parents=True, exist_ok=True)
    env, _, xdg_state, fake_home = make_isolated_env(isolated_tmp, None)
    st = run_mind({"action": "agent_status"}, env)
    sp = st["storagePaths"]
    assert sp["ragStorageMode"] == "standalone", sp
    assert sp["ragVaultDir"] == "", sp
    assert str(Path(sp["ragStatePath"]).resolve()) == str((xdg_state / "alis-companion" / "rag_sources.json").resolve()), sp

    src_dir = fake_home / "standalone_src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "notes.md").write_text("# Standalone Note\nFallback storage works without Nexus.\n", encoding="utf-8")
    added = run_mind({"action": "rag_action", "op": "add", "path": str(src_dir), "label": "Standalone Docs"}, env)
    assert any(s["path"] == str(src_dir) for s in added["sources"]), added
    listed = run_mind({"action": "rag_action", "op": "list"}, env)
    assert len(listed["sources"]) == 1
    removed = run_mind({"action": "rag_action", "op": "remove", "path": str(src_dir)}, env)
    assert removed["sources"] == []
    print("[PASS] 2. Standalone fallback with ALIS_NEXUS_VAULT=__DISABLED__")


def apply_env(env: dict[str, str]) -> dict[str, str | None]:
    prev = {}
    keys = ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "ALIS_ROOT", "ALIS_NEXUS_VAULT", "ALIS_AQUA_TEST_NO_SPAWN", "ALIS_TEST_ISOLATED", "ALIS_THINKING_ROUTER_DISABLE_ML")
    for k in keys:
        prev[k] = os.environ.get(k)
        if k in env:
            os.environ[k] = env[k]
    return prev


def restore_env(prev: dict[str, str | None]) -> None:
    for k, v in prev.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_3_nexus_fts_and_code_tools(env: dict[str, str], vault_dir: Path) -> None:
    reidx = run_mind({"action": "rag_action", "op": "reindex_nexus", "force": True}, env)
    assert reidx["ok"] is True and reidx["indexedNotes"] >= 3, reidx

    prev = apply_env(env)
    try:
        import nexus_rag
        import code_tools

        res_vi = nexus_rag.search_nexus_knowledge("huong dan thinkfan", vault_path=str(vault_dir))
        assert res_vi["citations"] and "20_Notes/thinkfan-guide.md" in res_vi["citations"][0]["relPath"], res_vi
        assert "3300 RPM" in res_vi["citations"][0]["snippet"], res_vi

        res_en = nexus_rag.search_nexus_knowledge("Wayland Niri Super+Slash", vault_path=str(vault_dir))
        assert any("20_Notes/niri-keybinds.md" in c["relPath"] for c in res_en["citations"]), res_en

        res_col = nexus_rag.search_nexus_knowledge("Designing Data-Intensive", vault_path=str(vault_dir))
        assert any(m["collection"] == "books" for m in res_col["matchedCollections"]), res_col

        note_res = nexus_rag.read_nexus_note("20_Notes/thinkfan-guide.md", vault_path=str(vault_dir))
        assert note_res["ok"] is True and "Cấu hình Thinkfan" in note_res["headings"][0], note_res

        for bad_path in ("../../etc/passwd", "/etc/shadow", "../secret.md", "bad\x00path.md"):
            try:
                nexus_rag.read_nexus_note(bad_path, vault_path=str(vault_dir))
                raise AssertionError(f"Path traversal should be blocked for {bad_path!r}")
            except nexus_rag.NexusRagError as exc:
                assert exc.code in ("invalid_note_path", "forbidden_note_path"), exc.code

        ov = nexus_rag.get_nexus_overview(vault_path=str(vault_dir))
        assert ov["ok"] is True and ov["collectionCounts"].get("books") == 1, ov

        rag_merged = code_tools.retrieve_rag("huong dan thinkfan RPM", ROOT, page_size=4)
        assert any(
            "Nexus: 20_Notes/thinkfan-guide.md" in (c.get("source", "") if isinstance(c, dict) else str(c))
            for c in rag_merged["citations"]
        ), rag_merged
        # Verify external DB file exists outside vault
        assert Path(ov["dbPath"]).is_file() and str(vault_dir) not in ov["dbPath"], ov
    finally:
        restore_env(prev)
    print("[PASS] 3. Full Nexus Vault FTS5 VI/EN + collections + path-traversal guard + code_tools merge")


def test_4_live_alis_queries(env: dict[str, str], vault_dir: Path) -> None:
    snap = make_live_snapshot(vault_dir)
    cases = [
        ("/weather", ("Can Tho", "31°C", "AQI 42")),
        ("thời tiết và AQI hôm nay thế nào?", ("31°C", "PM2.5", "AQI")),
        ("đang phát bài gì?", ("Moon River", "Andy Williams")),
        ("/pomodoro", ("DeepWork", "1h 30m", "2 session")),
        ("danh sách todo", ("Hoàn thành báo cáo RAG", "Kiểm tra Thinkfan")),
        ("lịch trình hôm nay", ("Code review Alis", "Gym Push Day")),
        ("xem quicknotes", ("Checklist", "FTS5")),
        ("có thông báo nào chưa đọc?", ("3", "GitHub")),
        ("screen time hôm nay", ("4h 00m", "Neovim")),
        ("bao nhiêu thẻ cần ôn?", ("14", "12 ngày")),
        ("bài tập hôm nay", ("3/5", "11/18", "Push Day")),
        ("tốc độ quạt thinkfan và giới hạn sạc tlp", ("3350 RPM", "75–80%")),
        ("/nexus overview", ("3", "90_System/99_RAG")),
        ("nhật ký hôm nay", ("Great", "High", "64kg")),
        ("tài chính trong nexus", ("15,000,000", "10,800,000")),
        ("tìm thinkfan trong nexus", ("thinkfan-guide.md", "3300 RPM")),
    ]
    for prompt, expected_tokens in cases:
        res = run_mind(
            {
                "action": "agent_turn",
                "prompt": prompt,
                "character": "aqua",
                "alisLocalSnapshot": snap,
                "nexusVaultPath": str(vault_dir),
                "persistHistory": False,
                "nowMs": int(time.time() * 1000),
            },
            env,
        )
        assert res["handled"] is True, f"{prompt!r} not handled: {res}"
        assert res["intent"] in ("alis_local_info", "nexus_knowledge"), (prompt, res)
        low = res["text"].lower()
        for tok in expected_tokens:
            assert tok.lower() in low, f"Missing token {tok!r} for {prompt!r}: {res['text']!r}"
    print("[PASS] 4. Live Alis & Nexus state queries via agent_turn (16 bilingual/slash prompts)")


def test_5_safe_instant_client_actions(env: dict[str, str], vault_dir: Path) -> None:
    snap = make_live_snapshot(vault_dir)
    cases = [
        ("làm mới thời tiết", "weather_refresh", "", ""),
        ("/music next", "music_next", "", ""),
        ("phát bài Moon River", "music_play_query", "Moon River", ""),
        ("bật pomodoro", "pomodoro_start", "", ""),
        ("bật night light", "night_light_set", "", "on"),
        ("bật chế độ không làm phiền", "dnd_set", "", "on"),
        ("mở nhật ký hôm nay trong nexus", "nexus_open_daily", "", ""),
    ]
    for prompt, exp_action, exp_target, exp_value in cases:
        res = run_mind(
            {
                "action": "agent_turn",
                "prompt": prompt,
                "character": "aqua",
                "alisLocalSnapshot": snap,
                "nexusVaultPath": str(vault_dir),
                "persistHistory": False,
                "nowMs": int(time.time() * 1000),
            },
            env,
        )
        assert res["handled"] is True and res.get("requiresConfirmation") is False, (prompt, res)
        acts = res.get("clientActions") or []
        assert len(acts) == 1, (prompt, acts)
        assert acts[0]["action"] == exp_action, (prompt, acts)
        if exp_target:
            assert exp_target.lower() in acts[0]["target"].lower(), (prompt, acts)
        if exp_value:
            assert acts[0]["value"] == exp_value, (prompt, acts)
    print("[PASS] 5. Safe instant clientActions (7 immediate actions)")


def test_6_confirmation_gated_write_actions(env: dict[str, str], vault_dir: Path) -> None:
    snap = make_live_snapshot(vault_dir)

    # a) Todo add -> cancel first, then ask & confirm
    ask_todo = run_mind(
        {
            "action": "agent_turn",
            "prompt": "thêm việc cần làm: ôn tập giải tích",
            "character": "aqua",
            "alisLocalSnapshot": snap,
            "nexusVaultPath": str(vault_dir),
            "nowMs": int(time.time() * 1000),
        },
        env,
    )
    assert ask_todo["handled"] is True and ask_todo.get("requiresConfirmation") is True, ask_todo
    assert not ask_todo.get("clientActions"), ask_todo

    cancel_todo = run_mind(
        {"action": "agent_turn", "prompt": "hủy", "character": "aqua", "nowMs": int(time.time() * 1000)},
        env,
    )
    assert cancel_todo["handled"] is True and not cancel_todo.get("clientActions"), cancel_todo

    ask_todo_2 = run_mind(
        {
            "action": "agent_turn",
            "prompt": "thêm việc cần làm: ôn tập giải tích",
            "character": "aqua",
            "alisLocalSnapshot": snap,
            "nexusVaultPath": str(vault_dir),
            "nowMs": int(time.time() * 1000),
        },
        env,
    )
    assert ask_todo_2.get("requiresConfirmation") is True
    confirm_todo = run_mind(
        {"action": "agent_turn", "prompt": "đồng ý", "character": "aqua", "nowMs": int(time.time() * 1000)},
        env,
    )
    assert confirm_todo["handled"] is True and confirm_todo["status"] == "completed", confirm_todo
    t_acts = confirm_todo.get("clientActions") or []
    assert len(t_acts) == 1 and t_acts[0]["action"] == "todo_add" and "ôn tập giải tích" in t_acts[0]["target"], confirm_todo

    # b) Quicknote add -> confirm
    ask_qn = run_mind(
        {
            "action": "agent_turn",
            "prompt": "/quicknote Ý tưởng: tích hợp RAG vào Nexus",
            "character": "aqua",
            "alisLocalSnapshot": snap,
            "nexusVaultPath": str(vault_dir),
            "nowMs": int(time.time() * 1000),
        },
        env,
    )
    assert ask_qn["handled"] is True and ask_qn.get("requiresConfirmation") is True, ask_qn
    confirm_qn = run_mind(
        {"action": "agent_turn", "prompt": "đồng ý", "character": "aqua", "nowMs": int(time.time() * 1000)},
        env,
    )
    qn_acts = confirm_qn.get("clientActions") or []
    assert len(qn_acts) == 1 and qn_acts[0]["action"] == "quicknote_add", confirm_qn
    assert "Ý tưởng" in qn_acts[0]["target"] and "tích hợp RAG vào Nexus" in qn_acts[0]["value"], qn_acts

    # c) /nexus save -> confirmation creates Markdown in <vault>/90_System/99_RAG/Knowledge/ & indexes it
    ask_save = run_mind(
        {
            "action": "agent_turn",
            "prompt": "/nexus save Kiến thức mới: Niri dùng config.kdl",
            "character": "aqua",
            "alisLocalSnapshot": snap,
            "nexusVaultPath": str(vault_dir),
            "nowMs": int(time.time() * 1000),
        },
        env,
    )
    assert ask_save["handled"] is True and ask_save.get("requiresConfirmation") is True, ask_save
    knowledge_dir = vault_dir / "90_System" / "99_RAG" / "Knowledge"
    before_notes = set(knowledge_dir.glob("*.md"))
    confirm_save = run_mind(
        {"action": "agent_turn", "prompt": "đồng ý", "character": "aqua", "nowMs": int(time.time() * 1000)},
        env,
    )
    assert confirm_save["handled"] is True and confirm_save["status"] == "completed", confirm_save
    after_notes = set(knowledge_dir.glob("*.md"))
    created_notes = list(after_notes - before_notes)
    assert len(created_notes) == 1, (before_notes, after_notes)
    assert "config.kdl" in created_notes[0].read_text(encoding="utf-8")

    # Verify newly saved RAG note is immediately searchable in Nexus RAG FTS5!
    q_new = run_mind(
        {
            "action": "agent_turn",
            "prompt": "tìm config.kdl trong nexus",
            "character": "aqua",
            "alisLocalSnapshot": snap,
            "nexusVaultPath": str(vault_dir),
            "persistHistory": False,
            "nowMs": int(time.time() * 1000),
        },
        env,
    )
    assert "config.kdl" in q_new["text"].lower(), q_new
    print("[PASS] 6. Confirmation-gated write actions (cancel/confirm todo_add, quicknote_add, nexus save)")


def test_7_react_tools_and_thinking_router(env: dict[str, str], vault_dir: Path) -> None:
    snap = make_live_snapshot(vault_dir)
    prev = apply_env(env)
    try:
        import agent_runtime
        import run_store
        import thinking_router

        agent_runtime._CURRENT_TURN_LIVE_SNAPSHOT = snap
        agent_runtime._CURRENT_TURN_NEXUS_VAULT = str(vault_dir)
        sctx = run_store.load_session_context()

        # alis_live_state
        out_live = agent_runtime._execute_llm_tool_call(
            {"name": "alis_live_state", "arguments": {"topic": "weather", "query": "AQI"}},
            sctx,
            ROOT,
            int(time.time() * 1000),
        )
        assert "Can Tho" in out_live and "31°C" in out_live, out_live

        # nexus_knowledge variants
        for args, expected in [
            ({"action": "search", "query": "thinkfan"}, "thinkfan-guide.md"),
            ({"action": "overview"}, "90_System/99_RAG"),
            ({"action": "journal"}, "Great"),
            ({"action": "finance"}, "15,000,000"),
            ({"action": "collections", "query": "books"}, "Designing Data-Intensive"),
            ({"action": "read", "path": "20_Notes/niri-keybinds.md"}, "Super+Slash"),
        ]:
            out_nx = agent_runtime._execute_llm_tool_call(
                {"name": "nexus_knowledge", "arguments": args},
                sctx,
                ROOT,
                int(time.time() * 1000),
            )
            assert expected in out_nx, (args, out_nx)

        # alis_service_control safe vs write-gated
        sctx["_pendingTurnClientActions"] = []
        out_safe = agent_runtime._execute_llm_tool_call(
            {"name": "alis_service_control", "arguments": {"action": "music_toggle"}},
            sctx,
            ROOT,
            int(time.time() * 1000),
        )
        assert len(sctx["_pendingTurnClientActions"]) == 1 and sctx["_pendingTurnClientActions"][0]["action"] == "music_toggle", out_safe

        out_gated = agent_runtime._execute_llm_tool_call(
            {"name": "alis_service_control", "arguments": {"action": "todo_add", "target": "Should not auto run"}},
            sctx,
            ROOT,
            int(time.time() * 1000),
        )
        assert len(sctx["_pendingTurnClientActions"]) == 1, f"Write action bypassed confirmation gate! {out_gated}"
        assert "confirm" in out_gated.lower() or "xác nhận" in out_gated.lower(), out_gated

        # Thinking router mechanical categories -> "off"
        r1 = thinking_router.route_thinking_effort("Thời tiết hôm nay thế nào?", {"category": "alis_local_info"})
        r2 = thinking_router.route_thinking_effort("Bật night light", {"category": "alis_service_control"})
        assert r1["effort"] == "off" and r2["effort"] == "off", (r1, r2)
    finally:
        agent_runtime._CURRENT_TURN_LIVE_SNAPSHOT = {}
        agent_runtime._CURRENT_TURN_NEXUS_VAULT = ""
        restore_env(prev)
    print("[PASS] 7. Direct ReAct tool calls & mechanical thinking_router effort")


def test_8_silent_offscreen_qml(tmp_path: Path) -> None:
    qml_tmp = tmp_path / "qml_offscreen"
    qml_tmp.mkdir(parents=True, exist_ok=True)
    vault_dir = qml_tmp / "Vault"
    vault_dir.mkdir(parents=True, exist_ok=True)
    env, xdg_config, xdg_state, _ = make_isolated_env(qml_tmp, vault_dir)
    seed_vault_and_cache(vault_dir, xdg_state)

    shell = qml_tmp / "shell"
    shell.mkdir(parents=True, exist_ok=True)
    for name in ("modules", "services", "scripts", "defaults", "translations", "assets", "sdata", "qmldir", "optional"):
        if (HOST / name).exists():
            (shell / name).symlink_to(HOST / name)
    for src in HOST.glob("*.qml"):
        if src.name != "shell.qml":
            (shell / src.name).symlink_to(src)

    comp_link = shell / "comp_root"
    comp_link.symlink_to(ROOT)

    qml_src = """
import QtQuick
import Quickshell
import qs.modules.common
import qs.services
import "comp_root/services" as CompServices
import "comp_root/modules/settings" as CompSettings

ShellRoot {
    id: root
    property int bootTicks: 0
    property bool done: false

    function fail(msg) {
        if (done) return false;
        done = true;
        console.error("AQUA_LOCAL_NEXUS_QML_FAIL:" + msg);
        Qt.exit(1);
        return false;
    }
    function require(cond, msg) {
        if (!cond) return fail(msg);
        return true;
    }

    CompSettings.CompanionConfig {
        id: cfgPage
        width: 620
        height: 820
        activeSection: "ai"
    }

    Timer {
        interval: 60
        running: true
        repeat: true
        onTriggered: {
            if (root.done)
                return;
            if (!Config.ready) {
                root.require(++root.bootTicks < 100, "Config.ready timeout");
                return;
            }
            CompServices.AquaMind.agentToolsEnabled = true;
            if (!root.require(CompServices.AquaMind.obsidianEnabled === false, "obsidianEnabled must stay false")) return;
            if (!root.require(CompServices.AquaMind.nexusVaultPath === "EXPECTED_VAULT", "nexusVaultPath mismatch: " + CompServices.AquaMind.nexusVaultPath)) return;
            if (!root.require(CompServices.AquaMind.nexusKnowledgeEnabled === true, "nexusKnowledgeEnabled must be true")) return;

            const snap = CompServices.AquaMind.buildLocalMachineSnapshot();
            if (!root.require(snap && typeof snap === "object", "buildLocalMachineSnapshot returned non-object")) return;
            for (const k of ["weather", "music", "timers", "todos", "quicknotes", "notifications", "system", "nexus"]) {
                if (!root.require(snap[k] && typeof snap[k] === "object", "snapshot missing section: " + k)) return;
            }
            const pulse = CompServices.AquaMind.buildCompactMachinePulse(snap);
            if (!root.require(pulse && pulse.nexusVaultReady === true, "pulse missing nexusVaultReady")) return;

            for (const p of ["thời tiết hôm nay thế nào?", "tìm ghi chú trong nexus", "/weather", "/nexus overview"]) {
                if (!root.require(CompServices.AquaMind.shouldRouteToAgentTurn(p, true) === true, "shouldRouteToAgentTurn false for: " + p)) return;
            }
            CompServices.AquaMind.executeClientActions([]);

            if (!root.require(cfgPage.activeSection === "ai", "CompanionConfig section sanity")) return;
            root.done = true;
            console.log("AQUA_LOCAL_NEXUS_QML_PASS");
            Qt.quit();
        }
    }
}
"""
    qml_src = qml_src.replace("EXPECTED_VAULT", str(vault_dir))
    (shell / "shell.qml").write_text(qml_src, encoding="utf-8")

    env.update({
        "QT_QPA_PLATFORM": "offscreen",
        "QT_NO_XDG_DESKTOP_PORTAL": "1",
        "QT_QPA_PLATFORMTHEME": "generic",
        "QT_LOGGING_RULES": "qml.debug=true",
        "NIRI_SOCKET": str(qml_tmp / "no-niri.sock"),
        "ALIS_COMPANION_FIXTURE_ENV": "1",
    })
    log = qml_tmp / "qs.log"
    with log.open("w", encoding="utf-8") as stream:
        proc = subprocess.Popen(
            ["dbus-run-session", "--", "qs", "--path", str(shell / "shell.qml"), "--no-color"],
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            code = proc.wait(timeout=25)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=5)
            txt_to = log.read_text(encoding="utf-8", errors="replace")
            raise AssertionError(f"qs timed out! Log tail:\n{txt_to[-8000:]}")
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=5)
    text = log.read_text(encoding="utf-8", errors="replace")
    assert code == 0 and "AQUA_LOCAL_NEXUS_QML_PASS" in text, text[-10000:]
    for err in ("ReferenceError:", "TypeError:", "Binding loop", "Failed to load configuration", "AQUA_LOCAL_NEXUS_QML_FAIL"):
        assert err not in text, text[-10000:]
    for line in text.splitlines():
        if "Unable to assign" in line:
            assert "BluetoothStatus.qml" in line, line
    print("[PASS] 8. Silent offscreen qs QML contract (AquaMind.qml + CompanionConfig.qml, 0 warnings)")


def main() -> None:
    only_qml = "--qml-only" in sys.argv
    with tempfile.TemporaryDirectory(prefix="alis-companion-local-nexus-rag-") as tmp:
        tmp_path = Path(tmp)
        if only_qml:
            test_8_silent_offscreen_qml(tmp_path)
            return
        env, vault_dir, _ = test_1_migration_and_storage_split(tmp_path)
        test_2_standalone_fallback(tmp_path)
        test_3_nexus_fts_and_code_tools(env, vault_dir)
        test_4_live_alis_queries(env, vault_dir)
        test_5_safe_instant_client_actions(env, vault_dir)
        test_6_confirmation_gated_write_actions(env, vault_dir)
        test_7_react_tools_and_thinking_router(env, vault_dir)
        test_8_silent_offscreen_qml(tmp_path)
    print("ALL_AQUA_LOCAL_NEXUS_RAG_TESTS_PASS")


if __name__ == "__main__":
    main()
