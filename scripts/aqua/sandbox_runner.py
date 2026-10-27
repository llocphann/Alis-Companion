"""Bubblewrap-isolated subprocess runner with process-group reaping and distinct exit states."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time


class SandboxError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


def bwrap_binary():
    override = os.environ.get("ALIS_AQUA_BWRAP_BIN", "").strip()
    if override:
        if override == "__MISSING__":
            return None
        return override if Path(override).is_file() and os.access(override, os.X_OK) else None
    found = shutil.which("bwrap") or ("/usr/bin/bwrap" if Path("/usr/bin/bwrap").is_file() else None)
    return found


def _build_bwrap_args(bwrap_bin, cwd, writable_dirs=None, readonly_dirs=None, allow_network=False):
    cmd = [
        bwrap_bin,
        "--ro-bind", "/usr", "/usr",
    ]
    for link_name, target in (("/lib", "usr/lib"), ("/lib64", "usr/lib64"), ("/bin", "usr/bin"), ("/sbin", "usr/bin")):
        p = Path(link_name)
        if p.is_symlink():
            cmd.extend(["--symlink", target, link_name])
        elif p.is_dir():
            cmd.extend(["--ro-bind", link_name, link_name])
    for etc_item in ("/etc/ld.so.cache", "/etc/alternatives", "/etc/localtime", "/etc/resolv.conf"):
        if Path(etc_item).exists():
            cmd.extend(["--ro-bind-try", etc_item, etc_item])
    # Cargo/rustup toolchain if present for rust-analyzer / cargo test
    home = Path.home().resolve(strict=False)
    for toolchain_dir in (home / ".cargo", home / ".rustup"):
        if toolchain_dir.is_dir():
            cmd.extend(["--ro-bind-try", str(toolchain_dir), str(toolchain_dir)])

    cmd.extend([
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--die-with-parent",
        "--new-session",
    ])
    if not allow_network:
        cmd.append("--unshare-net")

    ro_list = [Path(d).resolve(strict=False) for d in (readonly_dirs or []) if d]
    rw_list = [Path(d).resolve(strict=False) for d in (writable_dirs or []) if d]
    cwd_path = Path(cwd).resolve(strict=False)
    if cwd_path not in rw_list and cwd_path not in ro_list:
        ro_list.append(cwd_path)

    bound = set()
    for d in ro_list:
        s = str(d)
        if s not in bound and d.exists():
            cmd.extend(["--ro-bind-try", s, s])
            bound.add(s)
    for d in rw_list:
        s = str(d)
        if d.exists():
            cmd.extend(["--bind", s, s])
            bound.add(s)

    if cwd_path.is_dir():
        cmd.extend(["--chdir", str(cwd_path)])
    return cmd


def run_sandboxed(
    argv_or_script,
    cwd=None,
    writable_dirs=None,
    readonly_dirs=None,
    allow_network=False,
    timeout_sec=15.0,
    shell=False,
):
    bwrap_bin = bwrap_binary()
    if not bwrap_bin:
        raise SandboxError(
            "sandbox_unavailable",
            "Missing dependency: bubblewrap (bwrap) is required for sandboxed command execution.",
        )
    work_dir = Path(cwd or (Path.home() / "Desktop")).expanduser().resolve(strict=False)
    if not work_dir.is_dir():
        work_dir = Path.home().resolve(strict=False)

    timeout = max(0.5, min(180.0, float(timeout_sec or 15.0)))
    bwrap_prefix = _build_bwrap_args(
        bwrap_bin,
        cwd=work_dir,
        writable_dirs=writable_dirs,
        readonly_dirs=readonly_dirs,
        allow_network=allow_network,
    )
    if shell:
        inner_cmd = ["/usr/bin/bash", "-c", str(argv_or_script)]
    elif isinstance(argv_or_script, (list, tuple)):
        inner_cmd = [str(x) for x in argv_or_script]
    else:
        inner_cmd = ["/usr/bin/bash", "-c", str(argv_or_script)]

    full_cmd = bwrap_prefix + ["--"] + inner_cmd
    cargo_bin = str(Path.home() / ".cargo/bin")
    safe_env = {
        "PATH": f"{cargo_bin}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(Path.home().resolve(strict=False)),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TERM": "dumb",
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    started = time.monotonic()
    proc = subprocess.Popen(
        full_cmd,
        cwd=str(work_dir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=safe_env,
        start_new_session=True,
    )
    timed_out = False
    try:
        out_bytes, err_bytes = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            out_bytes, err_bytes = proc.communicate(timeout=0.5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            out_bytes, err_bytes = proc.communicate()
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.wait(timeout=1)

    elapsed_ms = int((time.monotonic() - started) * 1000)
    stdout_text = (out_bytes or b"").decode("utf-8", errors="replace")
    stderr_text = (err_bytes or b"").decode("utf-8", errors="replace")
    combined = (stdout_text + ("\n" + stderr_text if stderr_text.strip() else "")).strip()
    lines = [ln.strip()[:220] for ln in combined.splitlines() if ln.strip()][:24]

    if timed_out:
        return {
            "status": "timed_out",
            "timedOut": True,
            "exitCode": -1,
            "elapsedMs": elapsed_ms,
            "lines": lines or [f"(command timed out after {timeout:.1f}s; child processes reaped)"],
            "stdout": stdout_text[:4000],
            "stderr": stderr_text[:4000],
            "sandboxed": True,
        }
    exit_code = int(proc.returncode if proc.returncode is not None else -1)
    status = "completed" if exit_code == 0 else "failed"
    if not lines:
        lines = ["(completed with exit 0)" if exit_code == 0 else f"(failed with exit {exit_code})"]
    return {
        "status": status,
        "timedOut": False,
        "exitCode": exit_code,
        "elapsedMs": elapsed_ms,
        "lines": lines,
        "stdout": stdout_text[:4000],
        "stderr": stderr_text[:4000],
        "sandboxed": True,
    }
