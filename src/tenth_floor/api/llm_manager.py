"""vLLM lifecycle manager for the dashboard.

Local mode only: spawns the `vllm serve` subprocess from `.vllmenv`,
tracks its PID, polls `/v1/models` for readiness, and emits lifecycle
events on the shared event bus so the dashboard pill can react live.

Intentionally simple — one process at a time, in-memory state, no
restart-on-crash. Docker mode is a follow-up.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx

from tenth_floor.config import PROJECT_ROOT
from tenth_floor.events import EventType, event_bus

logger = logging.getLogger(__name__)

LLMState = Literal["offline", "starting", "ready", "failed"]

# Readiness polling
_POLL_INTERVAL_S = 2.0
_READY_TIMEOUT_S = 600.0  # 10 min — first-time model downloads can take a while
# Treat the process as ready when /v1/models responds 200 within this window.
_PROBE_TIMEOUT_S = 1.5


@dataclass
class _LLMStatus:
    state: LLMState = "offline"
    pid: int | None = None
    started_at: datetime | None = None
    model: str | None = None
    base_url: str = "http://localhost:8000/v1"
    last_error: str | None = None
    log_path: str | None = None

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "pid": self.pid,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "model": self.model,
            "base_url": self.base_url,
            "last_error": self.last_error,
            "log_path": self.log_path,
        }


@dataclass
class _Config:
    vllm_bin: Path
    model: str
    port: int
    max_model_len: int
    gpu_util: float
    max_num_seqs: int
    log_dir: Path
    base_url: str

    @property
    def health_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/models"


def _load_config() -> _Config:
    root = PROJECT_ROOT
    port = int(os.environ.get("VLLM_PORT", "8000"))
    host = os.environ.get("VLLM_HOST", "localhost")
    base_url = os.environ.get("LLM_BASE_URL", f"http://{host}:{port}/v1")
    return _Config(
        vllm_bin=root / ".vllmenv" / "bin" / "vllm",
        model=os.environ.get("VLLM_MODEL", "Qwen/Qwen3-32B-AWQ"),
        port=port,
        max_model_len=int(os.environ.get("VLLM_MAX_MODEL_LEN", "10240")),
        gpu_util=float(os.environ.get("VLLM_GPU_UTIL", "0.90")),
        max_num_seqs=int(os.environ.get("VLLM_MAX_NUM_SEQS", "32")),
        log_dir=root / "logs",
        base_url=base_url,
    )


def _probe_ready(url: str) -> bool:
    try:
        r = httpx.get(url, timeout=_PROBE_TIMEOUT_S)
        return r.status_code == 200
    except Exception:
        return False


class LLMManager:
    """Singleton: owns the vLLM subprocess lifecycle for this API process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status = _LLMStatus()
        self._proc: subprocess.Popen[bytes] | None = None
        self._watcher: threading.Thread | None = None
        # Reconcile initial state from env config (picks up existing vLLM).
        cfg = _load_config()
        self._status.model = cfg.model
        self._status.base_url = cfg.base_url
        if _probe_ready(cfg.health_url):
            self._status.state = "ready"

    # ---------- public API ----------

    def status(self) -> dict:
        """Return current status, refreshing the ready probe opportunistically."""
        with self._lock:
            cfg = _load_config()
            self._status.base_url = cfg.base_url
            # If we're tracking a process, check liveness.
            if self._proc is not None and self._proc.poll() is not None:
                # Process exited — if we were starting, mark failed; else offline.
                exit_code = self._proc.returncode
                if self._status.state == "starting":
                    self._status.state = "failed"
                    self._status.last_error = (
                        f"vLLM process exited with code {exit_code} before ready. "
                        f"Check {self._status.log_path}."
                    )
                elif self._status.state == "ready":
                    self._status.state = "offline"
                self._proc = None
                self._status.pid = None
            # Ready-probe short-circuit — if the server answers, we're ready
            # regardless of who started it.
            if self._status.state in ("offline", "starting", "failed"):
                if _probe_ready(cfg.health_url):
                    became_ready = self._status.state != "ready"
                    self._status.state = "ready"
                    self._status.last_error = None
                    if became_ready:
                        event_bus.emit(
                            "llm",
                            EventType.LLM_READY,
                            {"model": self._status.model, "base_url": cfg.base_url},
                        )
            return self._status.to_dict()

    def start(self) -> dict:
        """Spawn vLLM if not already running. Idempotent — returns current status."""
        with self._lock:
            cfg = _load_config()
            if _probe_ready(cfg.health_url):
                self._status.state = "ready"
                self._status.base_url = cfg.base_url
                self._status.model = cfg.model
                return self._status.to_dict()
            if self._status.state == "starting" and self._proc is not None:
                return self._status.to_dict()
            if not cfg.vllm_bin.is_file():
                self._status.state = "failed"
                self._status.last_error = f"vLLM binary not found at {cfg.vllm_bin}"
                return self._status.to_dict()

            cfg.log_dir.mkdir(parents=True, exist_ok=True)
            log_path = cfg.log_dir / "vllm.log"
            # Rotate yesterday's log aside so this run has a clean file.
            if log_path.exists():
                stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
                try:
                    log_path.rename(cfg.log_dir / f"vllm_{stamp}.log")
                except OSError:
                    pass

            log_file = open(log_path, "ab")  # noqa: SIM115 — owned by child process
            args = [
                str(cfg.vllm_bin), "serve", cfg.model,
                "--port", str(cfg.port),
                "--max-model-len", str(cfg.max_model_len),
                "--gpu-memory-utilization", str(cfg.gpu_util),
                "--max-num-seqs", str(cfg.max_num_seqs),
            ]
            logger.info("Starting vLLM: %s", " ".join(args))
            try:
                proc = subprocess.Popen(  # noqa: S603
                    args,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError as exc:
                log_file.close()
                self._status.state = "failed"
                self._status.last_error = f"Failed to spawn vLLM: {exc}"
                return self._status.to_dict()

            self._proc = proc
            self._status.state = "starting"
            self._status.pid = proc.pid
            self._status.started_at = datetime.now(UTC)
            self._status.model = cfg.model
            self._status.base_url = cfg.base_url
            self._status.last_error = None
            self._status.log_path = str(log_path)

            event_bus.emit(
                "llm",
                EventType.LLM_STARTING,
                {"pid": proc.pid, "model": cfg.model, "log_path": str(log_path)},
            )

            self._watcher = threading.Thread(
                target=self._watch_readiness,
                args=(cfg.health_url, cfg.model, cfg.base_url),
                daemon=True,
                name="llm-readiness-watcher",
            )
            self._watcher.start()
            return self._status.to_dict()

    def stop(self) -> dict:
        """Terminate the tracked subprocess (SIGTERM, then SIGKILL after 10s)."""
        with self._lock:
            proc = self._proc
            pid = self._status.pid
            if proc is None:
                # Nothing to stop — reconcile state.
                cfg = _load_config()
                self._status.state = "ready" if _probe_ready(cfg.health_url) else "offline"
                return self._status.to_dict()
            logger.info("Stopping vLLM (pid=%s)", pid)
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError) as exc:
                self._status.last_error = f"SIGTERM failed: {exc}"

        # Wait outside the lock so status() can still be called.
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

        with self._lock:
            self._proc = None
            self._status.pid = None
            self._status.state = "offline"
            event_bus.emit("llm", EventType.LLM_STOPPED, {"pid": pid})
            return self._status.to_dict()

    # ---------- internals ----------

    def _watch_readiness(self, health_url: str, model: str, base_url: str) -> None:
        """Poll /v1/models until the server answers or the process dies."""
        deadline = time.monotonic() + _READY_TIMEOUT_S
        while time.monotonic() < deadline:
            proc = self._proc
            if proc is None:
                return  # stopped externally
            if proc.poll() is not None:
                with self._lock:
                    if self._status.state == "starting":
                        self._status.state = "failed"
                        self._status.last_error = (
                            f"vLLM exited with code {proc.returncode} before ready. "
                            f"Check {self._status.log_path}."
                        )
                        self._proc = None
                        self._status.pid = None
                        event_bus.emit(
                            "llm",
                            EventType.LLM_FAILED,
                            {"message": self._status.last_error},
                        )
                return
            if _probe_ready(health_url):
                with self._lock:
                    self._status.state = "ready"
                    self._status.last_error = None
                event_bus.emit(
                    "llm",
                    EventType.LLM_READY,
                    {"model": model, "base_url": base_url},
                )
                return
            time.sleep(_POLL_INTERVAL_S)

        # Timed out without becoming ready.
        with self._lock:
            if self._status.state == "starting":
                self._status.state = "failed"
                self._status.last_error = (
                    f"vLLM did not become ready within {int(_READY_TIMEOUT_S)}s. "
                    f"Check {self._status.log_path}."
                )
                event_bus.emit(
                    "llm",
                    EventType.LLM_FAILED,
                    {"message": self._status.last_error},
                )


llm_manager = LLMManager()
