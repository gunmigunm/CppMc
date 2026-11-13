"""Java runtime handling for CppMc: detect an installed JDK, or fetch one.

Mojang ships no Java with the game, so a bare machine cannot start it. When no
usable runtime is present we download Eclipse Temurin (Adoptium) into
``%APPDATA%\\CppMc\\java\\temurin-<major>`` and reuse it afterwards.

Only the Python standard library is used.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from .core import (
    OS_ARCH,
    OS_NAME,
    USER_AGENT,
    LauncherError,
    app_dir,
    find_java,
    http_get_bytes,
    java_major_version,
)

ADOPTIUM_LATEST = (
    "https://api.adoptium.net/v3/assets/latest/{major}/hotspot"
    "?os={os}&architecture={arch}&image_type={kind}&vendor=eclipse"
)
ARCH_MAP = {"x86_64": "x64", "x86": "x86", "aarch64": "aarch64", "arm64": "aarch64"}
OS_MAP = {"windows": "windows", "osx": "mac", "linux": "linux"}
FALLBACK_MAJOR = 21

LogFn = Callable[[str], None]
ProgressFn = Callable[[str, int, int], None]


def java_root() -> Path:
    """Where downloaded runtimes live."""
    return app_dir() / "java"


def java_exe_name() -> str:
    return "java.exe" if os.name == "nt" else "java"


def bundled_java(major: int, root: Path | None = None) -> str | None:
    """Return the java executable of a previously downloaded runtime."""
    base = (Path(root) if root else java_root()) / f"temurin-{major}"
    exe = base / "bin" / java_exe_name()
    if exe.is_file():
        return str(exe)
    if not base.is_dir():
        return None
    # Adoptium zips unpack with one top level folder; tolerate both layouts.
    for child in sorted(base.iterdir()):
        candidate = child / "bin" / java_exe_name()
        if candidate.is_file():
            return str(candidate)
    return None


def _api_args() -> tuple[str, str]:
    return OS_MAP.get(OS_NAME, "linux"), ARCH_MAP.get(OS_ARCH, "x64")


def _latest_package(major: int, kind: str = "jre") -> tuple[str, dict]:
    os_name, arch = _api_args()
    url = ADOPTIUM_LATEST.format(major=major, os=os_name, arch=arch, kind=kind)
    try:
        payload = json.loads(http_get_bytes(url, timeout=60))
    except LauncherError:
        raise
    except json.JSONDecodeError as exc:
        raise LauncherError(f"无法解析 Adoptium 返回的数据：{exc}") from exc
    if not payload:
        raise LauncherError(
            f"Adoptium 上没有 Java {major} 的 {kind.upper()} 安装包（平台 {os_name}/{arch}）"
        )
    package = (payload[0].get("binary") or {}).get("package") or {}
    if not package.get("link"):
        raise LauncherError("Adoptium 返回的安装包信息不完整")
    return str(payload[0].get("version", {}).get("semver") or major), package


def _download_checked(url: str, dest: Path, sha256: str | None, size: int | None,
                      log: LogFn, progress: ProgressFn, label: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    done = 0
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response, open(dest, "wb") as handle:
        while True:
            chunk = response.read(1 << 18)
            if not chunk:
                break
            handle.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            if size:
                progress(label, done, size)
    if size and done != size:
        raise LauncherError(f"下载不完整：{url}（{done} / {size} 字节）")
    if sha256 and digest.hexdigest().lower() != sha256.lower():
        raise LauncherError(f"SHA256 校验失败：{dest.name}")


def _extract(zip_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.namelist():
            target = (destination / member).resolve()
            if not str(target).startswith(str(root)):
                raise LauncherError(f"压缩包路径非法：{member}")
        archive.extractall(destination)


def ensure_java(major: int, log: LogFn, progress: ProgressFn,
                kind: str = "jre", root: Path | None = None) -> str:
    """Make sure Java *major* is available, downloading Temurin when needed."""
    existing = bundled_java(major, root)
    if existing:
        log(f"使用已下载的 Java {major}：{existing}")
        return existing

    destination = (Path(root) if root else java_root()) / f"temurin-{major}"
    log(f"本机没有可用的 Java {major}，正在从 Adoptium 下载 Temurin {major}（{kind.upper()}）…")
    semver, package = _latest_package(major, kind)
    size = package.get("size")
    log(f"安装包：{package.get('name')}（{round((size or 0) / 1048576)} MB）")

    with tempfile.TemporaryDirectory(prefix="cppmc-java-") as temp:
        zip_path = Path(temp) / str(package.get("name") or f"java{major}.zip")
        progress("下载 Java", 0, int(size or 0))
        _download_checked(str(package["link"]), zip_path, package.get("checksum"),
                          int(size) if size else None, log, progress, "下载 Java")
        progress("下载 Java", int(size or 0), int(size or 0))
        log("正在解压…")
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        _extract(zip_path, destination)

    exe = bundled_java(major, root)
    if not exe:
        raise LauncherError(f"Java {major} 解压后找不到 java 可执行文件：{destination}")
    log(f"Java {semver} 已就绪：{exe}")
    return exe


def resolve_java(configured: str, required: int | None, log: LogFn,
                 progress: ProgressFn, allow_download: bool = True,
                 root: Path | None = None) -> tuple[str | None, int | None]:
    """Pick a runtime: configured path > our download > system > fresh download."""
    target = int(required or FALLBACK_MAJOR)

    if configured and Path(configured).is_file():
        major = java_major_version(configured)
        if major and major < target:
            log(f"提示：设置的 Java 是 Java {major}，该版本建议 Java {target}+")
        return configured, major

    found = bundled_java(target, root)
    if found:
        return found, java_major_version(found)

    system = find_java()
    if system:
        major = java_major_version(system)
        if major is None or major >= target:
            log(f"使用系统已有的 Java：{system}")
            return system, major
        log(f"系统 Java 只有 Java {major}，该版本需要 Java {target}+")

    if not allow_download:
        return (system, java_major_version(system)) if system else (None, None)

    exe = ensure_java(target, log, progress, root=root)
    return exe, java_major_version(exe)
