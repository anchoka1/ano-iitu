"""HTTPS-туннель для Mini App (Cloudflare Quick Tunnel).

Зачем: Telegram открывает Mini App только по HTTPS-адресу из интернета,
а наш сервер работает на http://localhost:8000. Программа cloudflared
создаёт временный адрес вида https://слова-слова.trycloudflare.com и
пересылает запросы с него на ваш компьютер. Регистрация не нужна.

Как это встроено в проект (TUNNEL=cloudflare в .env):
  1. при старте сервера ищем cloudflared (PATH, Program Files, папка tools/);
     если нет — скачиваем официальный файл с GitHub Cloudflare в tools/;
  2. запускаем `cloudflared tunnel --url http://localhost:8000`;
  3. читаем его вывод, пока не появится адрес https://...trycloudflare.com;
  4. подставляем адрес в настройки (WEBAPP_URL) — бот сразу показывает кнопки
     «Открыть приложение» и ставит кнопку меню.

Честно об ограничениях: адрес меняется при каждом запуске, а бесплатный
туннель не гарантирует стабильность — для учёбы и показа этого достаточно.
Для постоянного адреса нужен свой домен (Cloudflare named tunnel) или хостинг.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import re
import shutil
from pathlib import Path

from backend.app.core.config import PROJECT_ROOT

log = logging.getLogger("verdikt.tunnel")

TOOLS_DIR = PROJECT_ROOT / "tools"
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
DOWNLOADS = {
    "Windows": "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe",
    "Linux": "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
}


class TunnelError(Exception):
    pass


def find_cloudflared() -> Path | None:
    exe = "cloudflared.exe" if platform.system() == "Windows" else "cloudflared"
    candidates = [shutil.which("cloudflared"), TOOLS_DIR / exe,
                  Path("C:/Program Files (x86)/cloudflared/cloudflared.exe"), Path("C:/Program Files/cloudflared/cloudflared.exe")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


async def download_cloudflared() -> Path:
    """Скачивает cloudflared в tools/ (только официальный релиз Cloudflare на GitHub)."""
    import httpx

    url = DOWNLOADS.get(platform.system())
    if url is None:
        raise TunnelError("На macOS установите cloudflared сами: brew install cloudflared")
    TOOLS_DIR.mkdir(exist_ok=True)
    target = TOOLS_DIR / ("cloudflared.exe" if platform.system() == "Windows" else "cloudflared")
    log.info("Скачиваю cloudflared (≈ 60 МБ, один раз) …")
    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
        response = await client.get(url)
    if response.status_code != 200 or len(response.content) < 1_000_000:
        raise TunnelError(f"Не удалось скачать cloudflared (код {response.status_code}). Проверьте интернет.")
    target.write_bytes(response.content)
    target.chmod(0o755)
    return target


class Tunnel:
    def __init__(self, port: int) -> None:
        self.port = port
        self.url = ""
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None

    async def start(self, timeout: float = 45.0) -> str:
        exe = find_cloudflared() or await download_cloudflared()
        # --no-autoupdate: не пытаться обновляться во время работы
        self._process = await asyncio.create_subprocess_exec(
            str(exe), "tunnel", "--no-autoupdate", "--url", f"http://localhost:{self.port}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        found: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        async def read_output() -> None:
            # cloudflared пишет адрес в свой журнал; читаем строки, пока процесс жив.
            assert self._process and self._process.stdout
            async for raw in self._process.stdout:
                line = raw.decode(errors="replace")
                match = URL_RE.search(line)
                if match and not found.done():
                    found.set_result(match.group(0))
            if not found.done():
                found.set_exception(TunnelError("cloudflared завершился, не выдав адрес. Проверьте интернет."))

        self._reader = asyncio.create_task(read_output(), name="tunnel-reader")
        try:
            self.url = await asyncio.wait_for(found, timeout)
        except asyncio.TimeoutError as exc:
            await self.stop()
            raise TunnelError("cloudflared не выдал адрес за 45 секунд. Проверьте интернет/VPN.") from exc
        return self.url

    async def stop(self) -> None:
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), 5)
            except asyncio.TimeoutError:
                self._process.kill()
        if self._reader:
            self._reader.cancel()
