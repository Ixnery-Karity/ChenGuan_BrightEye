"""统一大模型客户端 —— 桌宠聊天 与 复盘分析 共用的唯一入口。

设计铁律（与本项目「离线优先，任意机器可演示」一致）：
  · 仅用 Python 标准库 urllib，不引入 openai / requests 等重依赖；
  · 自动探测后端，优先级：① OpenAI 兼容 API（含 DeepSeek 官方 / 硅基流动等，
    读环境变量）→ ② 本地 Ollama(localhost:11434) → ③ 都没有则不可用；
  · 任何异常/超时都安全返回 None，由调用方回退到离线规则逻辑，绝不阻塞 UI。

后端配置（择一即可，都没有就走离线）：
  A. 云端 API（OpenAI 兼容）：
     set BRIGHTEYE_LLM_BASE = https://api.deepseek.com/v1   (或其它兼容端点)
     set BRIGHTEYE_LLM_KEY  = sk-xxxx
  B. 本地 Ollama：安装后 `ollama serve` 默认监听 11434，
     `ollama pull qwen2.5:7b-instruct` / `ollama pull deepseek-r1:7b` 即可。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from typing import List, Optional

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

# 后端类型
_BACKEND_API = "openai"      # OpenAI 兼容 /chat/completions
_BACKEND_OLLAMA = "ollama"   # 本地 Ollama /api/chat
_BACKEND_NONE = "none"

# 自动拉起 Ollama：每个进程只尝试一次（多处 LLMClient 实例共享此标记）
_ollama_start_attempted = False


def _try_start_ollama() -> bool:
    """探测失败时后台拉起本地 Ollama 服务（`ollama serve`，无窗口分离进程）。

    找不到可执行文件 / 已尝试过 / 启动异常 → 返回 False，调用方照常降级离线。
    """
    global _ollama_start_attempted
    if _ollama_start_attempted:
        return False
    _ollama_start_attempted = True
    exe = shutil.which("ollama")
    if not exe and os.name == "nt":
        cand = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                            "Programs", "Ollama", "ollama.exe")
        if os.path.isfile(cand):
            exe = cand
    if not exe:
        return False
    try:
        kwargs = {}
        if os.name == "nt":
            # CREATE_NO_WINDOW(0x08000000)：不给子进程弹控制台窗口；
            # CREATE_NEW_PROCESS_GROUP(0x200)：脱离本进程的 Ctrl+C 组、软件退出后仍存活。
            # 注意：不能再叠加 DETACHED_PROCESS(0x8)——它与 CREATE_NO_WINDOW 同管
            # 控制台分配、按文档互斥，旧组合正是启动时黑窗闪现的来源之一。
            kwargs["creationflags"] = 0x08000000 | 0x00000200
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW   # 双保险：显式要求隐藏窗口
            si.wShowWindow = 0                              # SW_HIDE
            kwargs["startupinfo"] = si
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen([exe, "serve"],
                         stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, **kwargs)
        return True
    except Exception:
        return False


def strip_think(text: str) -> str:
    """剥离 DeepSeek-R1 等推理模型输出的 <think>…</think> 思维链，只留最终答复。"""
    if not text:
        return text
    cleaned = _THINK_RE.sub("", text)
    # 兜底：只有起始 <think> 而无闭合时，取最后一段
    if "<think>" in cleaned.lower():
        cleaned = cleaned.split("</think>")[-1].split("<think>")[-1]
    return cleaned.strip()


class LLMClient:
    """极简 LLM 客户端。探测一次后缓存后端，供多次调用复用。"""

    def __init__(self, base_url: str = "", api_key_env: str = "BRIGHTEYE_LLM_KEY",
                 ollama_host: str = "http://localhost:11434",
                 timeout_sec: float = 20.0, auto_start: bool = True):
        # base_url 优先取显式参数，其次环境变量（兼容 OPENAI_BASE_URL）
        self.base_url = (base_url
                         or os.environ.get("BRIGHTEYE_LLM_BASE")
                         or os.environ.get("OPENAI_BASE_URL")
                         or "").rstrip("/")
        self.api_key = os.environ.get(api_key_env) or os.environ.get("OPENAI_API_KEY") or ""
        self.ollama_host = ollama_host.rstrip("/")
        self.timeout = timeout_sec
        self.auto_start = auto_start          # 探测不到 Ollama 时是否自动拉起服务
        self._backend: Optional[str] = None   # 惰性探测缓存
        self._none_ts = 0.0                   # 上次判定「无后端」的时刻（供限速复检）

    # ---- 后端探测 ----------------------------------------------------
    def _probe_ollama(self, timeout: float = 2.0) -> bool:
        try:
            req = urllib.request.Request(self.ollama_host + "/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _detect(self) -> str:
        if self._backend is not None:
            # 「无后端」结论允许 30 秒后复检一次：覆盖 Ollama 慢启动 /
            # 用户中途手动开启服务的场景，成功后即升级为可用
            if self._backend == _BACKEND_NONE and time.time() - self._none_ts > 30.0:
                self._backend = None
            else:
                return self._backend
        # ① OpenAI 兼容 API：只要配了 base_url 即认为可用（真伪由实际调用兜底）
        if self.base_url:
            self._backend = _BACKEND_API
            return self._backend
        # ② 本地 Ollama：探测 /api/tags
        if self._probe_ollama(2.0):
            self._backend = _BACKEND_OLLAMA
            return self._backend
        # ②b 未运行 → 自动拉起本地 Ollama 服务并等它就绪（每进程只拉一次；
        #     调用方均在后台线程，等待不冻结 UI；实测冷启动就绪约 10~20s）
        if self.auto_start and _try_start_ollama():
            deadline = time.time() + 25.0
            while time.time() < deadline:
                time.sleep(0.5)
                if self._probe_ollama(1.0):
                    self._backend = _BACKEND_OLLAMA
                    return self._backend
        self._backend = _BACKEND_NONE
        self._none_ts = time.time()
        return self._backend

    def available(self) -> bool:
        """是否有可用的大模型后端。无 → 调用方走离线规则。"""
        return self._detect() != _BACKEND_NONE

    @property
    def backend(self) -> str:
        return self._detect()

    # ---- 统一对话接口 ------------------------------------------------
    def chat(self, messages: List[dict], model: str,
             temperature: float = 0.8, max_tokens: int = 512,
             timeout: Optional[float] = None) -> Optional[str]:
        """发一次对话，成功返回文本，失败/不可用返回 None（安全回退）。"""
        backend = self._detect()
        to = timeout if timeout is not None else self.timeout
        try:
            if backend == _BACKEND_API:
                return self._chat_openai(messages, model, temperature, max_tokens, to)
            if backend == _BACKEND_OLLAMA:
                return self._chat_ollama(messages, model, temperature, max_tokens, to)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError):
            return None
        except Exception:
            return None
        return None

    def _post_json(self, url: str, payload: dict, headers: dict, timeout: float) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        for k, v in headers.items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _chat_openai(self, messages, model, temperature, max_tokens, timeout) -> Optional[str]:
        url = self.base_url + "/chat/completions"
        headers = {}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        obj = self._post_json(url, payload, headers, timeout)
        return obj["choices"][0]["message"]["content"]

    def _chat_ollama(self, messages, model, temperature, max_tokens, timeout) -> Optional[str]:
        url = self.ollama_host + "/api/chat"
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        obj = self._post_json(url, payload, {}, timeout)
        return obj["message"]["content"]
