"""Native Windows OpenSSH, Slurm submission, and SOCKS forwarding helpers.

The NCHC account password and OTP are written to the interactive Windows
``ssh.exe`` process through a Windows pseudoterminal (ConPTY). They are never
added to the command line, written to a project file, or sent to FastAPI.
"""

from __future__ import annotations

import atexit
import base64
import os
import re
import secrets
import shlex
import shutil
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import PurePosixPath
import posixpath
from typing import Any, Callable

try:
    from winpty import PtyProcess
except ImportError:  # Windows-only optional dependency; surfaced clearly in UI.
    PtyProcess = None  # type: ignore[assignment]

NCHC_2FA_METHODS = {
    "1｜Mobile APP OTP（離線驗證碼）": "1",
    "2｜Mobile APP Push（推播核准）": "2",
    "3｜Email OTP（電子郵件驗證碼）": "3",
}
PROJECT_MARKER = "server/pathovision_server.py"
ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
RUNTIME_POLL_SECONDS = 2.0
SCHEDULER_POLL_SECONDS = 10.0
SCHEDULER_COMMAND_TIMEOUT_SECONDS = 8

TERMINAL_JOB_STATES = {
    "BOOT_FAIL",
    "CANCELLED",
    "COMPLETED",
    "DEADLINE",
    "FAILED",
    "NODE_FAIL",
    "OUT_OF_MEMORY",
    "PREEMPTED",
    "REVOKED",
    "SPECIAL_EXIT",
    "TIMEOUT",
}


class RemoteError(RuntimeError):
    pass


def _clean_terminal_text(value: str) -> str:
    value = ANSI_ESCAPE_RE.sub("", value)
    return value.replace("\r", "")


def choose_local_port(requested: int) -> int:
    requested = int(requested or 0)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", requested if requested > 0 else 0))
        return int(sock.getsockname()[1])


def _find_ssh_executable() -> str:
    candidates = [
        shutil.which("ssh"),
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", "OpenSSH", "ssh.exe"),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return os.path.abspath(candidate)
    raise RemoteError(
        "找不到 Windows OpenSSH Client（ssh.exe）。請至 Windows『選用功能』安裝 OpenSSH Client。"
    )


class _PtyChannel:
    """Thread-safe text transport around pywinpty's PtyProcess."""

    def __init__(self, process: Any):
        self.process = process
        self._buffer = ""
        self._condition = threading.Condition()
        self._closed = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        try:
            while self.is_alive():
                try:
                    chunk = self.process.read(4096)
                except TypeError:
                    chunk = self.process.read()
                if not chunk:
                    time.sleep(0.03)
                    continue
                if isinstance(chunk, bytes):
                    chunk = chunk.decode("utf-8", errors="replace")
                with self._condition:
                    self._buffer += _clean_terminal_text(str(chunk))
                    # Keep enough diagnostic context while avoiding unbounded growth.
                    if len(self._buffer) > 2_000_000:
                        self._buffer = self._buffer[-1_000_000:]
                    self._condition.notify_all()
        except (EOFError, OSError, RuntimeError):
            pass
        finally:
            with self._condition:
                self._closed = True
                self._condition.notify_all()

    def is_alive(self) -> bool:
        try:
            return bool(self.process.isalive())
        except Exception:
            return not self._closed

    def write_line(self, value: str) -> None:
        if not self.is_alive():
            raise RemoteError("Windows ssh.exe 已結束。")
        self.process.write(value + "\r")

    def clear(self) -> None:
        with self._condition:
            self._buffer = ""

    def wait_for_any(
        self,
        patterns: list[tuple[str, re.Pattern[str]]],
        timeout: float,
    ) -> tuple[str, str]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                for name, pattern in patterns:
                    match = pattern.search(self._buffer)
                    if match:
                        consumed = self._buffer[: match.end()]
                        self._buffer = self._buffer[match.end() :]
                        return name, consumed
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    tail = self._buffer[-1200:]
                    raise RemoteError(f"等待 Windows OpenSSH 回應逾時。\n{tail}")
                if self._closed and not self.is_alive():
                    tail = self._buffer[-1200:]
                    raise RemoteError(f"Windows ssh.exe 已提早結束。\n{tail}")
                self._condition.wait(timeout=min(0.25, remaining))

    def wait_for_regex(self, pattern: re.Pattern[str], timeout: float) -> tuple[re.Match[str], str]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                match = pattern.search(self._buffer)
                if match:
                    consumed = self._buffer[: match.end()]
                    self._buffer = self._buffer[match.end() :]
                    return match, consumed
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    tail = self._buffer[-2000:]
                    raise RemoteError(f"遠端命令等待逾時。\n{tail}")
                if self._closed and not self.is_alive():
                    tail = self._buffer[-2000:]
                    raise RemoteError(f"SSH 連線已結束。\n{tail}")
                self._condition.wait(timeout=min(0.25, remaining))

    def close(self) -> None:
        try:
            if self.is_alive():
                self.write_line("exit")
                time.sleep(0.2)
        except Exception:
            pass
        for method_name in ("close", "terminate", "kill"):
            method = getattr(self.process, method_name, None)
            if not callable(method):
                continue
            try:
                if method_name in {"close", "terminate"}:
                    try:
                        method(force=True)
                    except TypeError:
                        method()
                else:
                    method()
                break
            except Exception:
                continue
        self._reader.join(timeout=1.5)


class RemoteSSH:
    """Persistent authenticated Windows ssh.exe connection with a SOCKS tunnel."""

    AUTH_OK = "__PATHOVISION_AUTH_OK__"

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        channel: _PtyChannel,
        socks_port: int,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.channel = channel
        self.socks_port = socks_port
        self._command_lock = threading.Lock()

    @property
    def proxy_url(self) -> str:
        # socks5h resolves the compute-node hostname through NANO4, not Windows DNS.
        return f"socks5h://127.0.0.1:{self.socks_port}"

    @classmethod
    def connect(
        cls,
        host: str,
        port: int,
        username: str,
        password: str,
        method_label: str,
        otp: str,
        requested_socks_port: int = 0,
        timeout: float = 45.0,
    ) -> "RemoteSSH":
        if os.name != "nt":
            raise RemoteError("此 Client 的原生 OpenSSH 自動登入模式目前僅支援 Windows。")
        if PtyProcess is None:
            raise RemoteError("尚未安裝 pywinpty，請執行：pip install 'pywinpty>=2,<3'")

        host = (host or "").strip()
        username = (username or "").strip()
        password = password or ""
        otp = (otp or "").strip()
        method = NCHC_2FA_METHODS.get(method_label)
        if not host or not username or not password or method not in {"1", "2", "3"}:
            raise RemoteError("主機、帳號、密碼與 2FA 選項皆為必填。")
        if method in {"1", "3"} and not otp:
            raise RemoteError("此 2FA 方式需要 OTP 驗證碼。")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", username):
            raise RemoteError("NCHC 帳號格式無效。")
        if not (1 <= int(port) <= 65535):
            raise RemoteError("SSH Port 無效。")

        ssh_executable = _find_ssh_executable()
        socks_port = choose_local_port(requested_socks_port)
        remote_bootstrap = f"printf '{cls.AUTH_OK}\\n'; exec bash --noprofile --norc"
        args = [
            ssh_executable,
            "-4",
            "-T",
            "-D",
            f"127.0.0.1:{socks_port}",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=3",
            "-o",
            "PreferredAuthentications=keyboard-interactive,password",
            "-o",
            "PubkeyAuthentication=no",
            "-o",
            "NumberOfPasswordPrompts=1",
            "-p",
            str(int(port)),
            f"{username}@{host}",
            remote_bootstrap,
        ]
        try:
            process = PtyProcess.spawn(args, dimensions=(40, 140))
        except Exception as exc:
            raise RemoteError(f"無法啟動 Windows ssh.exe：{exc}") from exc

        channel = _PtyChannel(process)
        auth_timeout = 180.0 if method == "2" else max(timeout, 90.0)
        patterns = [
            ("success", re.compile(re.escape(cls.AUTH_OK))),
            ("host_changed", re.compile(r"(?is)REMOTE HOST IDENTIFICATION HAS CHANGED")),
            ("denied", re.compile(r"(?is)permission denied|authentication failed|access denied")),
            ("closed", re.compile(r"(?is)connection (?:closed|reset)|kex_exchange_identification|connection timed out|no route to host|could not resolve hostname")),
            ("method", re.compile(r"(?is)(?:2fa|two[- ]?factor|驗證方式|login method|authentication method|mobile app otp|email otp).{0,800}?(?:select|choice|請選擇|response|:)")),
            ("password", re.compile(r"(?is)(?:password|密碼)[^\n]{0,160}:")),
            ("otp", re.compile(r"(?is)(?:otp|verification code|verify code|passcode|token|驗證碼)[^\n]{0,180}:")),
            ("generic", re.compile(r"(?im)^(?:response|answer|choice|selection)[^\n]{0,100}:\s*$")),
        ]
        stage = 0
        deadline = time.monotonic() + auth_timeout
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RemoteError("NANO4 互動式登入逾時；Push 模式請確認手機是否已核准。")
                name, context = channel.wait_for_any(patterns, timeout=min(remaining, 30.0))
                if name == "success":
                    channel.clear()  # discard all authentication dialogue immediately
                    return cls(host, int(port), username, channel, socks_port)
                if name == "host_changed":
                    raise RemoteError(
                        "NANO4 SSH 主機金鑰與 known_hosts 不一致。請先人工確認後移除錯誤紀錄，勿直接略過檢查。"
                    )
                if name == "denied":
                    raise RemoteError("NCHC 驗證失敗，請檢查密碼、2FA 選項與 OTP。")
                if name == "closed":
                    raise RemoteError(f"NANO4 關閉原生 OpenSSH 連線。\n{context[-800:]}")
                if name == "method":
                    channel.write_line(method)
                    stage = max(stage, 1)
                elif name == "password":
                    channel.write_line(password)
                    stage = max(stage, 2)
                elif name == "otp":
                    channel.write_line("" if method == "2" else otp)
                    stage = max(stage, 3)
                elif name == "generic":
                    if stage <= 0:
                        channel.write_line(method)
                        stage = 1
                    elif stage == 1:
                        channel.write_line(password)
                        stage = 2
                    else:
                        channel.write_line("" if method == "2" else otp)
                        stage = 3
        except Exception:
            channel.close()
            raise

    def close(self) -> None:
        self.channel.close()

    def exec(self, command: str, timeout: float = 60.0) -> tuple[int, str, str]:
        """Run one command through the persistent remote bash process."""
        token = uuid.uuid4().hex
        # Keep framing markers and shell metacharacters out of the text typed
        # into ConPTY.  The complete wrapper is Base64-encoded first, so the
        # Windows pseudoterminal only receives one ASCII-safe launcher line.
        begin = f"PVBEGIN{token}"
        end = f"PVEND{token}"
        payload = base64.b64encode(command.encode("utf-8")).decode("ascii")
        remote_script = (
            "set +e\n"
            f"printf '\\n{begin}\\n'\n"
            f"printf '%s' {shlex.quote(payload)} | base64 -d | bash 2>&1\n"
            "rc=$?\n"
            f"printf '\\n{end}:%s\\n' \"$rc\"\n"
        )
        launcher_payload = base64.b64encode(remote_script.encode("utf-8")).decode("ascii")
        launcher = f"printf %s {launcher_payload} | base64 -d | bash"
        end_pattern = re.compile(re.escape(end) + r":(-?\d+)(?:\n|$)")
        with self._command_lock:
            self.channel.clear()
            self.channel.write_line(launcher)
            match, consumed = self.channel.wait_for_regex(end_pattern, timeout=timeout)
        status = int(match.group(1))

        normalized = consumed.replace("\r\n", "\n").replace("\r", "\n")
        output_end = normalized.rfind(end)
        begin_index = normalized.rfind(begin, 0, output_end)
        if begin_index < 0 or output_end < 0 or begin_index >= output_end:
            raise RemoteError(
                "無法解析遠端命令輸出標記。請重新建立 OpenSSH 連線後再試。"
            )
        output_start = begin_index + len(begin)
        output = normalized[output_start:output_end].strip("\n")
        if status == 0:
            return status, output.strip(), ""
        return status, "", output.strip()

    def exec_ok(self, command: str, timeout: float = 60.0) -> str:
        status, stdout, stderr = self.exec(command, timeout)
        if status != 0:
            raise RemoteError(stderr or stdout or f"Remote command failed ({status}).")
        return stdout

    def write_text(self, remote_path: str, content: str, mode: int = 0o600) -> None:
        parent = posixpath.dirname(remote_path)
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        temp = remote_path + ".tmp"
        command = (
            f"mkdir -p {shlex.quote(parent)} && chmod 700 {shlex.quote(parent)} && "
            f"printf '%s' {shlex.quote(encoded)} | base64 -d > {shlex.quote(temp)} && "
            f"chmod {mode:o} {shlex.quote(temp)} && mv {shlex.quote(temp)} {shlex.quote(remote_path)}"
        )
        self.exec_ok(command, timeout=120)

    def read_text(self, remote_path: str) -> str:
        return self.exec_ok(f"cat {shlex.quote(remote_path)}", timeout=30)


def discover_projects(ssh: RemoteSSH) -> list[str]:
    """快速檢查標準部署位置，避免遞迴掃描大型 /work 目錄。"""
    script = r'''
set -eu
for project in \
  "${PATHOVISION_PROJECT_DIR:-}" \
  "$HOME/2026_NCHC_Summer_Intern_Project" \
  "/work/$USER/2026_NCHC_Summer_Intern_Project"
do
  [ -n "$project" ] || continue
  if [ -f "$project/server/pathovision_server.py" ]; then
    printf '%s\n' "$project"
  fi
done
'''
    output = ssh.exec_ok("bash -lc " + shlex.quote(script), timeout=15)
    return sorted({line.strip() for line in output.splitlines() if line.strip()})


def discover_accounts(ssh: RemoteSSH) -> list[str]:
    """Account 為可選欄位；查詢過慢時直接回傳空清單，不阻塞登入。"""
    command = "bash -lc " + shlex.quote(
        "sacctmgr -n -P show assoc user=$USER format=Account 2>/dev/null | sed '/^$/d' | sort -u"
    )
    try:
        status, stdout, _stderr = ssh.exec(command, timeout=8)
    except RemoteError:
        return []
    return [line.strip() for line in stdout.splitlines() if line.strip()] if status == 0 else []


def shell_single(value: str) -> str:
    return shlex.quote(value)


def build_sbatch(
    project_dir: str,
    api_key: str,
    partition: str,
    account: str,
    walltime: str,
    cpus: int,
    memory_gb: int,
    gpu_count: int,
) -> str:
    account_line = f"#SBATCH --account={account}\n" if account.strip() else ""
    return f'''#!/usr/bin/env bash
#SBATCH --job-name=pathovision-vlm-ui
#SBATCH --partition={partition}
{account_line}#SBATCH --time={walltime}
#SBATCH --cpus-per-task={cpus}
#SBATCH --mem={memory_gb}G
#SBATCH --gres=gpu:{gpu_count}
#SBATCH --output={project_dir}/.pathovision_runtime/slurm-%j.out
#SBATCH --error={project_dir}/.pathovision_runtime/slurm-%j.err

set -euo pipefail
umask 077
PROJECT_DIR={shell_single(project_dir)}
RUNTIME_DIR="$PROJECT_DIR/.pathovision_runtime"
mkdir -p "$RUNTIME_DIR"
chmod 700 "$RUNTIME_DIR"
cd "$PROJECT_DIR"

STACK_SCRIPT="$PROJECT_DIR/slurm/pathovision_vlm_stack.sbatch"
if [ ! -f "$STACK_SCRIPT" ]; then
  echo "找不到整合式 YOLO + 分析推論模型啟動檔：$STACK_SCRIPT" >&2
  exit 1
fi

export PATHOVISION_PROJECT_DIR="$PROJECT_DIR"
export PATHOVISION_API_KEY={shell_single(api_key)}
export PATHOVISION_DEFAULT_STUDENT_MODEL=""
exec bash "$STACK_SCRIPT"
'''


@dataclass
class ManagedSession:
    token: str
    ssh: RemoteSSH
    username: str
    projects: list[str]
    accounts: list[str]
    job_id: str = ""
    project_dir: str = ""
    runtime_env: str = ""
    api_key: str = ""
    node: str = ""
    server_port: int = 0
    local_port: int = 0
    created_at: float = field(default_factory=time.time)
    job_submitted_at: float = 0.0

    @property
    def proxy_url(self) -> str:
        return self.ssh.proxy_url

    def close(self, cancel_job: bool = False) -> None:
        if cancel_job and self.job_id:
            try:
                self.ssh.exec(f"scancel {shlex.quote(self.job_id)}", timeout=20)
            except Exception:
                pass
        self.ssh.close()


_SESSIONS: dict[str, ManagedSession] = {}
_SESSIONS_LOCK = threading.Lock()


def create_managed_session(
    host: str,
    port: int,
    username: str,
    password: str,
    method: str,
    otp: str,
    local_port: int = 0,
) -> ManagedSession:
    ssh = RemoteSSH.connect(
        host,
        port,
        username,
        password,
        method,
        otp,
        requested_socks_port=int(local_port or 0),
    )
    try:
        projects = discover_projects(ssh)
        accounts = discover_accounts(ssh)
        token = uuid.uuid4().hex
        session = ManagedSession(
            token,
            ssh,
            username,
            projects,
            accounts,
            local_port=ssh.socks_port,
        )
        with _SESSIONS_LOCK:
            _SESSIONS[token] = session
        return session
    except Exception:
        ssh.close()
        raise


def get_session(token: str) -> ManagedSession:
    with _SESSIONS_LOCK:
        session = _SESSIONS.get(token)
    if session is None:
        raise RemoteError("NCHC 工作階段不存在或已失效，請重新登入。")
    return session


def submit_server_job(
    session: ManagedSession,
    project_dir: str,
    partition: str,
    account: str,
    walltime: str,
    cpus: int,
    memory_gb: int,
    gpu_count: int,
) -> str:
    project_dir = project_dir.rstrip("/")
    if project_dir not in session.projects:
        raise RemoteError("請選擇掃描結果中的有效專案。")
    if any(char.isspace() for char in project_dir) or "\n" in project_dir or "\r" in project_dir:
        raise RemoteError("目前版本不支援含空白或換行的遠端專案路徑。")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", partition or ""):
        raise RemoteError("Partition 格式無效。")
    if account and not re.fullmatch(r"[A-Za-z0-9_.-]+", account):
        raise RemoteError("Account 格式無效。")
    if not re.fullmatch(r"[0-9:-]+", walltime or ""):
        raise RemoteError("Walltime 格式無效。")
    if not (1 <= int(cpus) <= 256 and 1 <= int(memory_gb) <= 4096 and 0 <= int(gpu_count) <= 16):
        raise RemoteError("Slurm 資源參數超出允許範圍。")
    if int(gpu_count) != 3:
        raise RemoteError("Gemma + Mistral + YOLO 整合服務固定需要 3 張 GPU。")
    cpu_limit = int(gpu_count) * 12
    if int(cpus) > cpu_limit:
        raise RemoteError(
            f"NANO4 每張 GPU 最多配置 12 CPU；{gpu_count} 張 GPU 的 CPU 上限為 {cpu_limit}。"
        )
    api_key = secrets.token_urlsafe(36)
    runtime_dir = f"{project_dir}/.pathovision_runtime"
    script_path = f"{runtime_dir}/submit-{int(time.time())}.sbatch"
    content = build_sbatch(
        project_dir,
        api_key,
        partition,
        account,
        walltime,
        cpus,
        memory_gb,
        gpu_count,
    )
    session.ssh.write_text(script_path, content, mode=0o700)
    output = session.ssh.exec_ok(f"sbatch --parsable {shlex.quote(script_path)}", timeout=30)

    # Expected output is "123456" or "123456;cluster".  Keep a guarded
    # fallback for terminals that inject harmless prompt/echo lines.
    job_match = re.search(r"(?m)^\s*(\d+)(?:;[^\r\n]+)?\s*$", output)
    if not job_match:
        job_match = re.search(r"(?m)\bSubmitted\s+batch\s+job\s+(\d+)\b", output)
    if not job_match:
        raise RemoteError(f"無法解析 sbatch Job ID：{output}")
    job_id = job_match.group(1)
    session.job_id = job_id
    session.project_dir = project_dir
    session.runtime_env = f"{runtime_dir}/{job_id}.env"
    session.api_key = api_key
    session.job_submitted_at = time.time()
    return job_id


def parse_env(content: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in content.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if re.fullmatch(r"[A-Z_][A-Z0-9_]*", key):
            result[key] = value
    return result


def _read_runtime_env(session: ManagedSession) -> dict[str, str]:
    """Read the small stack status file without waiting on Slurm commands."""
    if not session.runtime_env:
        return {}
    command = (
        f"if [ -r {shlex.quote(session.runtime_env)} ]; then "
        f"cat {shlex.quote(session.runtime_env)}; fi"
    )
    try:
        status, stdout, _stderr = session.ssh.exec(command, timeout=8)
    except Exception:
        return {}
    return parse_env(stdout) if status == 0 else {}


def _endpoint_from_env(session: ManagedSession, env: dict[str, str]) -> str:
    node = env.get("NODE", "").strip()
    port_text = env.get("PORT", "").strip()
    if not node or not port_text.isdigit():
        raise RemoteError("Runtime 狀態檔缺少 Compute Node 或 Server Port。")
    port = int(port_text)
    if not (1 <= port <= 65535):
        raise RemoteError("Runtime 狀態檔的 Server Port 無效。")
    session.node = node
    session.server_port = port
    return f"http://{node}:{port}"


def _normalize_slurm_state(value: str) -> str:
    """Normalize values such as ``CANCELLED by 123`` or ``FAILED+``."""
    value = (value or "").strip().upper()
    if not value:
        return ""
    return re.split(r"[\s+]", value, maxsplit=1)[0]


def _read_job_diagnostics(session: ManagedSession, max_lines: int = 80) -> str:
    """Return the most useful Slurm/API logs for a terminal Job."""
    if not session.project_dir or not session.job_id:
        return ""
    runtime_dir = f"{session.project_dir}/.pathovision_runtime"
    paths = [
        f"{runtime_dir}/slurm-{session.job_id}.err",
        f"{runtime_dir}/slurm-{session.job_id}.out",
        f"{runtime_dir}/server-{session.job_id}.log",
        f"{runtime_dir}/gemma4-vllm-{session.job_id}.log",
        f"{runtime_dir}/mistral-vllm-{session.job_id}.log",
        f"{runtime_dir}/phi35-vllm-{session.job_id}.log",
    ]
    quoted_paths = " ".join(shlex.quote(path) for path in paths)
    script = f"""
for file in {quoted_paths}; do
  if [ -s "$file" ]; then
    printf '\\n===== %s =====\\n' "$file"
    tail -n {int(max_lines)} "$file"
  fi
done
"""
    try:
        status, stdout, stderr = session.ssh.exec(
            "bash -lc " + shlex.quote(script),
            timeout=30,
        )
    except Exception as exc:
        return f"無法讀取 Job log：{exc}"
    output = (stdout or stderr or "").strip()
    return output or "尚未找到 Slurm stderr、stdout 或 Server log。"


def job_status(
    session: ManagedSession,
    *,
    runtime_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not session.job_id:
        raise RemoteError("尚未提交 Slurm Job。")

    job_id = session.job_id
    state = ""
    nodelist = ""
    reason = ""
    exit_code = ""

    # Active Jobs are visible in squeue.
    # NANO4 scheduler queries can occasionally stall. Bound them remotely so
    # the persistent ConPTY shell always emits its PVEND framing marker.
    queue_command = (
        f"timeout {SCHEDULER_COMMAND_TIMEOUT_SECONDS}s "
        f"squeue -h -j {shlex.quote(job_id)} -o '%T|%N|%R'"
    )
    queue_status, queue_stdout, queue_stderr = session.ssh.exec(
        queue_command,
        timeout=SCHEDULER_COMMAND_TIMEOUT_SECONDS + 4,
    )
    if queue_status == 124:
        raise RemoteError("squeue 查詢逾時，稍後自動重試。")
    queue_line = next(
        (line.strip() for line in queue_stdout.splitlines() if line.strip()),
        "",
    )
    if queue_status == 0 and queue_line:
        queue_parts = queue_line.split("|", 2)
        state = _normalize_slurm_state(queue_parts[0])
        nodelist = queue_parts[1].strip() if len(queue_parts) > 1 else ""
        reason = queue_parts[2].strip() if len(queue_parts) > 2 else ""
    else:
        # Completed/failed Jobs disappear from squeue. Ask accounting instead of
        # inventing the ambiguous COMPLETED_OR_MISSING state.
        accounting_command = (
            f"timeout {SCHEDULER_COMMAND_TIMEOUT_SECONDS}s "
            f"sacct -n -P -j {shlex.quote(job_id)} "
            "--format=JobIDRaw,State,ExitCode,NodeList 2>/dev/null"
        )
        acct_status, acct_stdout, acct_stderr = session.ssh.exec(
            accounting_command,
            timeout=SCHEDULER_COMMAND_TIMEOUT_SECONDS + 4,
        )
        if acct_status == 124:
            raise RemoteError("sacct 查詢逾時，稍後自動重試。")
        if acct_status == 0:
            for line in acct_stdout.splitlines():
                parts = [part.strip() for part in line.split("|")]
                if len(parts) < 4 or parts[0] != job_id:
                    continue
                state = _normalize_slurm_state(parts[1])
                exit_code = parts[2]
                nodelist = parts[3]
                break

        if state:
            reason = f"ExitCode={exit_code}" if exit_code else ""
        else:
            elapsed = (
                time.time() - session.job_submitted_at
                if session.job_submitted_at
                else 0.0
            )
            state = "SUBMITTED" if elapsed < 20 else "UNKNOWN"
            reason = (
                "等待 Slurm 將 Job 登錄到 squeue/sacct"
                if state == "SUBMITTED"
                else (
                    acct_stderr
                    or queue_stderr
                    or "squeue 與 sacct 都查不到此 Job"
                )
            )

    env = _read_runtime_env(session) if runtime_env is None else runtime_env

    if state == "RUNNING" and nodelist:
        session.node = nodelist

    return {
        "state": state,
        "nodelist": nodelist,
        "reason": reason,
        "exit_code": exit_code,
        "env": env,
    }


def establish_tunnel(session: ManagedSession, requested_local_port: int = 0) -> str:
    """Resolve the compute-node API address; SOCKS already runs on the SSH session."""
    del requested_local_port
    env = _read_runtime_env(session)
    if env.get("READY") == "1":
        return _endpoint_from_env(session, env)

    info = job_status(session, runtime_env=env)
    env = info["env"]
    if env.get("READY") != "1":
        # The stack can become ready while the scheduler command is in flight.
        env = _read_runtime_env(session)
    if env.get("READY") != "1":
        raise RemoteError(
            f"Server 尚未就緒；Job 狀態：{info['state']}，原因/節點：{info['reason'] or info['nodelist']}"
        )
    return _endpoint_from_env(session, env)


def _wait_status_text(info: dict[str, Any], env: dict[str, str]) -> str:
    state = str(info.get("state") or "SUBMITTED")
    location = str(info.get("nodelist") or info.get("reason") or "").strip()
    runtime_stage = next(
        (
            env.get(key, "").strip()
            for key in ("STAGE", "PHASE", "STATUS", "MESSAGE")
            if env.get(key, "").strip()
        ),
        "",
    )
    parts = [state]
    if location:
        parts.append(location)
    if runtime_stage:
        parts.append(runtime_stage.replace("\r", " ").replace("\n", " ")[:200])
    elif state == "RUNNING":
        parts.append("正在初始化 REST Server／分析推論模型")
    return " · ".join(parts)


def wait_and_establish_tunnel(
    session: ManagedSession,
    requested_local_port: int,
    timeout_seconds: int = 1800,
    on_update: Callable[[str], None] | None = None,
    endpoint_probe: Callable[[str], bool] | None = None,
) -> str:
    del requested_local_port
    deadline = time.monotonic() + timeout_seconds
    next_scheduler_poll = 0.0
    info: dict[str, Any] = {
        "state": "SUBMITTED",
        "nodelist": "",
        "reason": "等待 Slurm 將 Job 登錄",
        "env": {},
    }

    while time.monotonic() < deadline:
        env = _read_runtime_env(session)
        if env.get("READY") == "1":
            return _endpoint_from_env(session, env)

        # The REST process can already be serving requests before the Slurm
        # stack writes its final READY=1 marker (for example while optional VLM
        # workers are still completing their startup bookkeeping).  Prefer an
        # actual endpoint probe whenever NODE and PORT are available so the UI
        # does not remain stuck at RUNNING despite a usable connection.
        if endpoint_probe and env.get("NODE") and env.get("PORT"):
            try:
                endpoint = _endpoint_from_env(session, env)
                endpoint_is_ready = endpoint_probe(endpoint)
            except Exception:
                endpoint_is_ready = False
            if endpoint_is_ready:
                if on_update:
                    on_update("CONNECTED · REST API 已回應")
                return endpoint

        now = time.monotonic()
        if now >= next_scheduler_poll:
            try:
                info = job_status(session, runtime_env=env)
            except RemoteError as exc:
                info = {
                    **info,
                    "state": "SCHEDULER_QUERY_RETRY",
                    "reason": str(exc),
                    "env": env,
                }
                next_scheduler_poll = now + 5.0
            else:
                next_scheduler_poll = time.monotonic() + SCHEDULER_POLL_SECONDS

        text = _wait_status_text(info, env)
        if on_update:
            on_update(text)

        if info["state"] in TERMINAL_JOB_STATES:
            diagnostics = _read_job_diagnostics(session)
            raise RemoteError(
                f"Slurm Job 已結束但 REST Server 未就緒：{text}\n\n{diagnostics}"
            )

        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(RUNTIME_POLL_SECONDS, remaining))
    raise RemoteError("等待 Server 就緒逾時；Job 仍保留，可按『重新檢查』。")


def close_session(token: str, cancel_job: bool = False) -> None:
    with _SESSIONS_LOCK:
        session = _SESSIONS.pop(token, None)
    if session:
        session.close(cancel_job=cancel_job)


def close_all_sessions(cancel_jobs: bool = True) -> None:
    """關閉所有 ManagedSession；預設取消其 Slurm Job 以歸還資源。"""
    with _SESSIONS_LOCK:
        sessions = list(_SESSIONS.values())
        _SESSIONS.clear()
    for session in sessions:
        try:
            session.close(cancel_job=cancel_jobs)
        except Exception:
            # Interpreter shutdown 階段不應因單一連線清理失敗阻止其他 Job 清理。
            pass


def _cleanup() -> None:
    # 正常程式退出與 Ctrl+C 的最後一道保護。
    close_all_sessions(cancel_jobs=True)


atexit.register(_cleanup)
