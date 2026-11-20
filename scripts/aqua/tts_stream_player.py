#!/usr/bin/env python3
"""Detached streaming sentence-chunked TTS player for Alis-Companion.

Plays pre-synthesized chunk 0 immediately while synthesizing subsequent chunks
one step ahead on a background worker thread using the single canonical
multilingual voice (`en-US-AvaMultilingualNeural`), ensuring uninterrupted
playback and instant SIGTERM process-group teardown.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

_ACTIVE_CHILD: subprocess.Popen | None = None
_STOPPING = False


def _cleanup_child() -> None:
    global _ACTIVE_CHILD
    proc = _ACTIVE_CHILD
    _ACTIVE_CHILD = None
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=1.5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def _handle_signal(_signum: int, _frame) -> None:
    global _STOPPING
    _STOPPING = True
    _cleanup_child()
    raise SystemExit(0)


def _synth_edge_mp3(text: str, out_mp3: Path, voice: str, rate: str, edge_cli: str) -> bool:
    if _STOPPING or not text.strip():
        return False
    for attempt_rate in (rate, "+0%"):
        try:
            import edge_tts

            async def _run():
                comm = edge_tts.Communicate(text, voice, rate=attempt_rate)
                await asyncio.wait_for(comm.save(str(out_mp3)), timeout=8.5)

            asyncio.run(_run())
            if out_mp3.is_file() and out_mp3.stat().st_size > 128:
                return True
        except Exception:
            pass
    if edge_cli and not _STOPPING:
        for attempt_rate in (rate, "+0%"):
            try:
                subprocess.run(
                    [edge_cli, "--voice", voice, "--rate", attempt_rate, "--text", text, "--write-media", str(out_mp3)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=9.0,
                    check=False,
                )
                if out_mp3.is_file() and out_mp3.stat().st_size > 128:
                    return True
            except Exception:
                pass
    return False


def _prepare_chunk(
    idx: int,
    chunk_text: str,
    chunk_dir: Path,
    voice: str,
    rate: str,
    edge_cli: str,
    play_bin: str,
    ffmpeg_bin: str,
) -> tuple[Path | None, list[Path]]:
    out_mp3 = chunk_dir / f"tts_chunk_{idx:03d}.mp3"
    out_wav = chunk_dir / f"tts_chunk_{idx:03d}.wav"
    temps = [out_mp3, out_wav]
    if not _synth_edge_mp3(chunk_text, out_mp3, voice, rate, edge_cli):
        return None, temps
    if play_bin and ffmpeg_bin:
        try:
            subprocess.run(
                [ffmpeg_bin, "-y", "-loglevel", "quiet", "-i", str(out_mp3), str(out_wav)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=4.5,
                check=False,
            )
            if out_wav.is_file() and out_wav.stat().st_size > 64:
                return out_wav, temps
        except Exception:
            pass
    return out_mp3, temps


def _play_file(path: Path, play_bin: str, ffplay_bin: str) -> None:
    global _ACTIVE_CHILD
    if _STOPPING or not path or not path.is_file():
        return
    if path.suffix.lower() == ".wav" and play_bin:
        cmd = [play_bin, str(path)]
    elif ffplay_bin:
        cmd = [ffplay_bin, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)]
    elif play_bin:
        cmd = [play_bin, str(path)]
    else:
        return
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _ACTIVE_CHILD = proc
    try:
        proc.wait(timeout=45.0)
    except Exception:
        if proc.poll() is None:
            proc.terminate()
    finally:
        if _ACTIVE_CHILD is proc:
            _ACTIVE_CHILD = None


def main() -> int:
    if len(sys.argv) < 2:
        return 1
    manifest_path = Path(sys.argv[1])
    if not manifest_path.is_file():
        return 1

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    to_unlink: list[Path] = [manifest_path]
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        first_audio = Path(str(data.get("firstAudioPath") or ""))
        first_mp3 = Path(str(data.get("firstMp3Path") or "")) if data.get("firstMp3Path") else None
        remaining = [str(c) for c in (data.get("remainingChunks") or []) if str(c).strip()]
        voice = str(data.get("voice") or "en-US-AvaMultilingualNeural")
        rate = str(data.get("rate") or "+4%")
        chunk_dir = Path(str(data.get("chunkDir") or manifest_path.parent))
        play_bin = str(data.get("playBin") or "")
        ffmpeg_bin = str(data.get("ffmpegBin") or "")
        ffplay_bin = str(data.get("ffplayBin") or "")
        edge_cli = str(data.get("edgeCli") or "")

        if first_audio.is_file():
            to_unlink.append(first_audio)
        if first_mp3 and first_mp3.is_file():
            to_unlink.append(first_mp3)

        futures = []
        with ThreadPoolExecutor(max_workers=1) as pool:
            for i, chunk_txt in enumerate(remaining, start=1):
                futures.append(
                    pool.submit(
                        _prepare_chunk,
                        i,
                        chunk_txt,
                        chunk_dir,
                        voice,
                        rate,
                        edge_cli,
                        play_bin,
                        ffmpeg_bin,
                    )
                )

            if first_audio.is_file() and not _STOPPING:
                _play_file(first_audio, play_bin, ffplay_bin)

            for fut in futures:
                if _STOPPING:
                    break
                audio_file, temps = fut.result()
                to_unlink.extend(temps)
                if audio_file and audio_file.is_file() and not _STOPPING:
                    _play_file(audio_file, play_bin, ffplay_bin)
        return 0
    finally:
        _cleanup_child()
        for p in to_unlink:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
