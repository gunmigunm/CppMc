"""Core launcher logic for CppMc.

This module contains everything that is not GUI: version metadata, downloads,
library / native / asset handling, command line building and process launch.
Only the Python standard library is used.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

APP_NAME = "CppMc"
APP_BRAND = "C++mc"
APP_VERSION = "1.0.0"

MANIFEST_URLS = (
    "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json",
    "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json",
)
ASSET_BASE_URL = "https://resources.download.minecraft.net/"
USER_AGENT = f"{APP_NAME}/{APP_VERSION}"

LogFn = Callable[[str], None]
ProgressFn = Callable[[str, int, int], None]


class LauncherError(RuntimeError):
    """Any failure that should be shown to the user as-is."""


# --------------------------------------------------------------------------- #
# platform helpers
# --------------------------------------------------------------------------- #

OS_NAME = "windows" if os.name == "nt" else ("osx" if sys.platform == "darwin" else "linux")


def _normalise_arch() -> str:
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64", "x64"}:
        return "x86_64"
    if machine in {"x86", "i386", "i486", "i586", "i686"}:
        return "x86"
    return machine


OS_ARCH = _normalise_arch()


def app_dir() -> Path:
    """Per-user directory used for config files."""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


CONFIG_PATH = app_dir() / "config.json"
DEFAULT_GAME_DIR = app_dir() / "game"


# --------------------------------------------------------------------------- #
# small utilities
# --------------------------------------------------------------------------- #

def file_sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def http_get_bytes(url: str, timeout: int = 60, retries: int = 3) -> bytes:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as exc:  # noqa: BLE001 - network errors are all reported alike
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(1.5 * (attempt + 1))
    raise LauncherError(f"请求失败：{url}（{last_error}）")


def download_file(
    url: str,
    dest: Path,
    sha1: str | None = None,
    size: int | None = None,
    timeout: int = 120,
    retries: int = 3,
) -> bool:
    """Download *url* to *dest*. Returns True when the file was written."""
    if dest.exists():
        if sha1:
            if file_sha1(dest) == sha1:
                return False
        elif size is not None and dest.stat().st_size == size:
            return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    last_error: Exception | None = None

    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response, open(part, "wb") as handle:
                shutil.copyfileobj(response, handle)
            if sha1:
                got = file_sha1(part)
                if got != sha1:
                    raise LauncherError(f"SHA1 校验失败：{dest.name}（{got} != {sha1}）")
            part.replace(dest)
            return True
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            with contextlib.suppress(OSError):
                part.unlink()
            if attempt + 1 < retries:
                time.sleep(1.5 * (attempt + 1))

    raise LauncherError(f"下载失败：{url}（{last_error}）")


def offline_uuid(username: str) -> str:
    """Deterministic offline UUID, identical to vanilla offline mode."""
    digest = hashlib.md5(("OfflinePlayer:" + username).encode("utf-8")).hexdigest()
    return digest


def maven_to_path(name: str) -> str:
    """``group:artifact:version[:classifier]`` -> relative jar path."""
    parts = name.split(":")
    if len(parts) < 3:
        return name.replace(":", "/")
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) > 3 else None
    file_name = f"{artifact}-{version}" + (f"-{classifier}" if classifier else "") + ".jar"
    return "/".join(group.split(".") + [artifact, version, file_name])


# --------------------------------------------------------------------------- #
# rule handling
# --------------------------------------------------------------------------- #

def _rule_matches(os_rule: dict | None, feature_rule: dict | None, features: dict[str, bool]) -> bool:
    if os_rule:
        name = os_rule.get("name")
        if name and name != OS_NAME:
            return False
        arch = os_rule.get("arch")
        if arch and arch != OS_ARCH:
            return False
        version = os_rule.get("version")
        if version:
            try:
                if not re.match(version, platform.version()):
                    return False
            except re.error:
                pass
    if feature_rule:
        for key, expected in feature_rule.items():
            if bool(features.get(key, False)) != bool(expected):
                return False
    return True


def rules_allow(rules: Iterable[dict] | None, features: dict[str, bool]) -> bool:
    """Evaluate a Mojang ``rules`` list. Missing rules mean "allowed"."""
    if not rules:
        return True
    allowed = False
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if _rule_matches(rule.get("os"), rule.get("features"), features):
            allowed = rule.get("action") == "allow"
    return allowed


def collect_arguments(items: Iterable[Any], features: dict[str, bool]) -> list[str]:
    """Flatten a Mojang ``arguments`` list (strings + conditional objects)."""
    result: list[str] = []
    for item in items or []:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            if rules_allow(item.get("rules"), features):
                value = item.get("value")
                if isinstance(value, list):
                    result.extend(str(v) for v in value)
                elif value is not None:
                    result.append(str(value))
    return result


# --------------------------------------------------------------------------- #
# java discovery
# --------------------------------------------------------------------------- #

JAVA_SEARCH_DIRS = (
    r"C:\Program Files\Java",
    r"C:\Program Files\Eclipse Adoptium",
    r"C:\Program Files\AdoptOpenJDK",
    r"C:\Program Files\Microsoft",
    r"C:\Program Files\Amazon Corretto",
    r"C:\Program Files\Zulu",
    r"C:\Program Files\BellSoft",
    r"C:\Program Files (x86)\Java",
)


def find_java(configured: str = "") -> str | None:
    """Best effort search for a usable java executable."""
    if configured:
        candidate = Path(configured)
        if candidate.is_file():
            return str(candidate)

    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        exe = Path(java_home) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if exe.is_file():
            return str(exe)

    which = shutil.which("java")
    if which:
        return which

    if os.name == "nt":
        for base in JAVA_SEARCH_DIRS:
            folder = Path(base)
            if not folder.is_dir():
                continue
            try:
                children = sorted(folder.iterdir(), reverse=True)
            except OSError:
                continue
            for child in children:
                exe = child / "bin" / "java.exe"
                if exe.is_file():
                    return str(exe)
    return None


def java_major_version(java_path: str) -> int | None:
    """Return the major version of *java_path* (21 for ``21.0.3``)."""
    try:
        completed = subprocess.run(
            [java_path, "-version"],
            capture_output=True,
            text=True,
            timeout=20,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (completed.stderr or "") + (completed.stdout or "")
    match = re.search(r'version "?(\d+)(?:\.(\d+))?', output)
    if not match:
        return None
    major = int(match.group(1))
    if major == 1 and match.group(2):  # 1.8.0 -> 8
        return int(match.group(2))
    return major


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #

DEFAULT_CONFIG: dict[str, Any] = {
    "username": "Player",
    "version": "",
    "java_path": "",
    "memory_mb": 2048,
    "game_dir": str(DEFAULT_GAME_DIR),
    "fullscreen": False,
    "width": 0,
    "height": 0,
}


def load_config() -> dict[str, Any]:
    data = dict(DEFAULT_CONFIG)
    try:
        stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            for key, value in stored.items():
                if key in data:
                    data[key] = value
    except (OSError, json.JSONDecodeError):
        pass
    return data


def save_config(config: dict[str, Any]) -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# launcher
# --------------------------------------------------------------------------- #

@dataclass
class Prepared:
    """Everything needed to build a launch command for one version."""

    version_id: str
    version_json: dict[str, Any]
    client_jar: Path | None = None
    classpath: list[Path] = field(default_factory=list)
    natives_dir: Path | None = None
    assets_root: Path | None = None
    assets_index: str = ""
    log_config: Path | None = None
    main_class: str = "net.minecraft.client.main.Main"

    @property
    def java_major_required(self) -> int | None:
        info = self.version_json.get("javaVersion") or {}
        value = info.get("majorVersion")
        return int(value) if isinstance(value, int) else None

    @property
    def uses_modern_arguments(self) -> bool:
        return "arguments" in self.version_json


class Launcher:
    """Download / install / launch one Minecraft version at a time."""

    def __init__(
        self,
        game_dir: str | Path,
        log: LogFn | None = None,
        progress: ProgressFn | None = None,
        workers: int = 16,
    ) -> None:
        self.game_dir = Path(game_dir).expanduser().resolve()
        self.log: LogFn = log or (lambda message: None)
        self.progress: ProgressFn = progress or (lambda label, current, total: None)
        self.workers = max(1, workers)

        self.versions_dir = self.game_dir / "versions"
        self.libraries_dir = self.game_dir / "libraries"
        self.assets_dir = self.game_dir / "assets"
        self._manifest: dict[str, Any] | None = None

    # ---------------------------------------------------------------- metadata
    def manifest(self, refresh: bool = False) -> dict[str, Any]:
        if self._manifest is None or refresh:
            last_error: Exception | None = None
            for url in MANIFEST_URLS:
                try:
                    payload = json.loads(http_get_bytes(url, timeout=30))
                    if not isinstance(payload, dict) or "versions" not in payload:
                        raise LauncherError("版本列表格式异常")
                    self._manifest = payload
                    self.log(f"已获取版本列表（共 {len(payload['versions'])} 个版本）")
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
            else:
                raise LauncherError(f"无法获取版本列表：{last_error}")
        return self._manifest

    def version_entries(self, refresh: bool = False) -> list[dict[str, Any]]:
        return list(self.manifest(refresh).get("versions", []))

    def version_ids(self, refresh: bool = False) -> list[str]:
        return [entry["id"] for entry in self.version_entries(refresh) if "id" in entry]

    def resolve_version(self, version_id: str, refresh: bool = False) -> dict[str, Any]:
        for entry in self.version_entries(refresh):
            if entry.get("id") == version_id:
                return entry
        raise LauncherError(f"找不到版本：{version_id}")

    def version_json(self, version_id: str, entry: dict[str, Any] | None = None) -> dict[str, Any]:
        entry = entry or self.resolve_version(version_id)
        meta_file = self.versions_dir / version_id / f"{version_id}.json"
        if meta_file.exists():
            try:
                cached = json.loads(meta_file.read_text(encoding="utf-8"))
                if isinstance(cached, dict) and cached.get("id") == version_id:
                    return cached
            except (OSError, json.JSONDecodeError):
                pass
        payload = json.loads(http_get_bytes(entry["url"], timeout=30))
        if not isinstance(payload, dict):
            raise LauncherError(f"版本 JSON 格式异常：{version_id}")
        meta_file.parent.mkdir(parents=True, exist_ok=True)
        meta_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    # ---------------------------------------------------------------- features
    @staticmethod
    def features(resolution: tuple[int, int] | None = None) -> dict[str, bool]:
        return {
            "is_demo_user": False,
            "has_custom_resolution": bool(resolution),
            "has_quick_plays_support": False,
            "is_quick_play_singleplayer": False,
            "is_quick_play_multiplayer": False,
            "is_quick_play_realms": False,
        }

    # ---------------------------------------------------------------- install
    def install(self, version_id: str, download: bool = True) -> Prepared:
        entry = self.resolve_version(version_id)
        version_json = self.version_json(version_id, entry)
        if version_json.get("inheritsFrom"):
            raise LauncherError(
                f"版本 {version_id} 使用了 inheritsFrom（Forge/Fabric 等模组加载器），CppMc 暂不支持。"
            )

        prepared = Prepared(version_id=version_id, version_json=version_json)
        prepared.main_class = str(version_json.get("mainClass") or "net.minecraft.client.main.Main")

        # ---- client jar
        client = (version_json.get("downloads") or {}).get("client") or {}
        jar_path = self.versions_dir / version_id / f"{version_id}.jar"
        if client.get("url"):
            if download:
                self.log("下载客户端 jar …")
                download_file(client["url"], jar_path, client.get("sha1"), client.get("size"))
            prepared.client_jar = jar_path
        elif jar_path.exists():
            prepared.client_jar = jar_path
        else:
            self.log("提示：该版本没有独立的客户端 jar（1.6 之前的部分版本使用启动器自身的压缩包结构）")

        # ---- libraries + natives
        prepared.classpath, prepared.natives_dir = self._install_libraries(
            version_json, version_id, download
        )
        if prepared.client_jar:
            prepared.classpath.append(prepared.client_jar)

        # ---- assets
        prepared.assets_index, prepared.assets_root = self._install_assets(version_json, download)

        # ---- logging config
        prepared.log_config = self._install_logging(version_json, download)
        return prepared

    def _install_libraries(
        self, version_json: dict[str, Any], version_id: str, download: bool
    ) -> tuple[list[Path], Path]:
        features = self.features()
        classpath: list[Path] = []
        natives_dir = self.versions_dir / version_id / "natives"
        native_jars: list[tuple[Path, dict[str, Any]]] = []
        tasks: list[tuple[str, Path, str | None, int | None]] = []

        libraries = version_json.get("libraries") or []
        self.progress("依赖库", 0, len(libraries))
        seen: set[str] = set()

        for index, library in enumerate(libraries, start=1):
            if not rules_allow(library.get("rules"), features):
                self.progress("依赖库", index, len(libraries))
                continue

            name = str(library.get("name") or "")
            downloads = library.get("downloads") or {}

            # legacy natives (Minecraft <= 1.18): classifier jar extracted into natives/
            natives = library.get("natives")
            if isinstance(natives, dict):
                key = natives.get(OS_NAME)
                if key:
                    key = key.replace("${arch}", "64" if OS_ARCH == "x86_64" else "32")
                    classifier = (downloads.get("classifiers") or {}).get(key)
                    if classifier and classifier.get("url"):
                        path = self.libraries_dir / classifier["path"]
                        tasks.append((classifier["url"], path, classifier.get("sha1"), classifier.get("size")))
                        native_jars.append((path, library.get("extract") or {}))

            artifact = downloads.get("artifact")
            if artifact and artifact.get("url"):
                path = self.libraries_dir / artifact["path"]
                tasks.append((artifact["url"], path, artifact.get("sha1"), artifact.get("size")))
                if str(path) not in seen:
                    seen.add(str(path))
                    classpath.append(path)
            elif library.get("url") and name:
                relative = maven_to_path(name)
                path = self.libraries_dir / relative
                tasks.append((library["url"].rstrip("/") + "/" + relative, path, None, None))
                if str(path) not in seen:
                    seen.add(str(path))
                    classpath.append(path)

            self.progress("依赖库", index, len(libraries))

        if download and tasks:
            failures: list[str] = []
            with ThreadPoolExecutor(max_workers=min(self.workers, 8)) as pool:
                futures = {pool.submit(download_file, url, dest, sha1, size): dest for url, dest, sha1, size in tasks}
                for future in as_completed(futures):
                    try:
                        future.result()
                    except LauncherError as exc:
                        failures.append(str(exc))
            if failures:
                raise LauncherError(f"{len(failures)} 个依赖库下载失败，例如：{failures[0]}")

        # extract legacy natives
        if native_jars:
            natives_dir.mkdir(parents=True, exist_ok=True)
            for jar_path, extract in native_jars:
                if jar_path.exists():
                    self._extract_natives(jar_path, natives_dir, extract.get("exclude") or ["META-INF/"])

        self.progress("依赖库", len(libraries), len(libraries))
        return classpath, natives_dir

    @staticmethod
    def _extract_natives(jar_path: Path, destination: Path, excludes: Iterable[str]) -> None:
        patterns = [str(item) for item in excludes]
        try:
            with zipfile.ZipFile(jar_path) as archive:
                for member in archive.namelist():
                    if member.endswith("/"):
                        continue
                    if any(member.startswith(pattern) for pattern in patterns):
                        continue
                    target = destination / Path(member).name
                    with contextlib.suppress(OSError):
                        target.write_bytes(archive.read(member))
        except (OSError, zipfile.BadZipFile) as exc:
            raise LauncherError(f"解压 natives 失败：{jar_path.name}（{exc}）") from exc

    def _install_assets(self, version_json: dict[str, Any], download: bool) -> tuple[str, Path]:
        index_info = version_json.get("assetIndex")
        if not index_info:
            self.log("该版本没有资源索引（1.6 之前的老版本），跳过资源下载")
            return str(version_json.get("assets") or ""), self.assets_dir

        index_id = str(index_info.get("id") or index_info.get("assets") or "legacy")
        index_file = self.assets_dir / "indexes" / f"{index_id}.json"
        if download:
            download_file(index_info["url"], index_file, index_info.get("sha1"), index_info.get("size"))
        if not index_file.exists():
            raise LauncherError(f"缺少资源索引文件：{index_file}")

        try:
            index_data = json.loads(index_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LauncherError(f"资源索引无法解析：{exc}") from exc

        objects = index_data.get("objects") or {}
        tasks: list[tuple[str, Path, str, int | None]] = []
        for relative, info in objects.items():
            object_hash = str(info.get("hash") or "")
            if len(object_hash) < 4:
                continue
            destination = self.assets_dir / "objects" / object_hash[:2] / object_hash
            if destination.exists():
                expected = info.get("size")
                if expected is None or destination.stat().st_size == expected:
                    continue
            tasks.append(
                (f"{ASSET_BASE_URL}{object_hash[:2]}/{object_hash}", destination, object_hash, info.get("size"))
            )

        total = len(tasks)
        self.log(f"资源文件：{len(objects)} 个索引条目，需要下载 {total} 个")

        # Very old indexes are not hash-addressed at runtime: 1.5 and earlier read
        # ``<game>/resources``, 1.6/1.7 read ``assets/virtual/<index>``.
        if index_data.get("map_to_resources"):
            assets_root = self.game_dir / "resources"
        elif index_data.get("virtual"):
            assets_root = self.assets_dir / "virtual" / index_id
        else:
            assets_root = self.assets_dir

        if total and not download:
            self.progress("资源文件", total, total)
            return index_id, assets_root

        if total:
            self.progress("资源文件", 0, total)
            done = 0
            failures: list[str] = []
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                futures = {
                    pool.submit(download_file, url, dest, sha1, size): dest
                    for url, dest, sha1, size in tasks
                }
                for future in as_completed(futures):
                    done += 1
                    try:
                        future.result()
                    except LauncherError as exc:
                        failures.append(str(exc))
                    if done % 50 == 0 or done == total:
                        self.progress("资源文件", done, total)

            if failures:
                raise LauncherError(f"{len(failures)} 个资源文件下载失败，例如：{failures[0]}")

        if index_data.get("map_to_resources") or index_data.get("virtual"):
            copied = self._map_assets(objects, assets_root)
            self.log(f"已把 {copied} 个资源按原始文件名整理到 {assets_root}（老版本需要这种布局）")

        return index_id, assets_root

    def _map_assets(self, objects: dict[str, Any], destination_root: Path) -> int:
        """Copy hash-addressed objects to their original file names (legacy layout)."""
        copied = 0
        for relative, info in objects.items():
            object_hash = str(info.get("hash") or "")
            if len(object_hash) < 4:
                continue
            source = self.assets_dir / "objects" / object_hash[:2] / object_hash
            target = destination_root / relative
            if not source.exists() or target.exists():
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                copied += 1
            except OSError:
                continue
        return copied

    def _install_logging(self, version_json: dict[str, Any], download: bool) -> Path | None:
        client = (version_json.get("logging") or {}).get("client") or {}
        file_info = client.get("file") or {}
        if not file_info.get("url"):
            return None
        destination = self.assets_dir / "log_configs" / str(file_info.get("id") or "client.xml")
        if download:
            download_file(file_info["url"], destination, file_info.get("sha1"), file_info.get("size"))
        return destination if destination.exists() else None

    # ---------------------------------------------------------------- command
    def build_command(
        self,
        prepared: Prepared,
        username: str,
        java_path: str,
        memory_mb: int = 2048,
        resolution: tuple[int, int] | None = None,
        fullscreen: bool = False,
        extra_jvm: Iterable[str] | None = None,
    ) -> list[str]:
        version_json = prepared.version_json
        features = self.features(resolution)

        classpath = [str(path) for path in prepared.classpath]
        classpath_string = os.pathsep.join(classpath)

        substitutions = {
            "auth_player_name": username,
            "version_name": prepared.version_id,
            "game_directory": str(self.game_dir),
            "assets_root": str(prepared.assets_root or self.assets_dir),
            "game_assets": str(prepared.assets_root or self.assets_dir),
            "assets_index_name": prepared.assets_index,
            "auth_uuid": offline_uuid(username),
            "auth_access_token": "0",
            "auth_session": "-",
            "user_type": "legacy",
            "version_type": APP_BRAND,
            "natives_directory": str(prepared.natives_dir or (self.versions_dir / prepared.version_id / "natives")),
            "launcher_name": APP_NAME,
            "launcher_version": APP_VERSION,
            "classpath": classpath_string,
            "library_directory": str(self.libraries_dir),
            "classpath_separator": os.pathsep,
            "user_properties": "{}",
            "resolution_width": str(resolution[0]) if resolution else "",
            "resolution_height": str(resolution[1]) if resolution else "",
        }

        def substitute(arguments: Iterable[str]) -> list[str]:
            result = []
            for argument in arguments:
                text = str(argument)
                for key, value in substitutions.items():
                    text = text.replace("${" + key + "}", value)
                result.append(text)
            return result

        if prepared.uses_modern_arguments:
            arguments = version_json.get("arguments") or {}
            jvm_arguments = substitute(collect_arguments(arguments.get("jvm"), features))
            game_arguments = substitute(collect_arguments(arguments.get("game"), features))
        else:
            jvm_arguments = substitute(
                [
                    "-Djava.library.path=${natives_directory}",
                    "-cp",
                    "${classpath}",
                ]
            )
            game_arguments = substitute(
                (version_json.get("minecraftArguments") or "").split()
            )

        if prepared.log_config is not None:
            jvm_arguments.insert(0, f"-Dlog4j.configurationFile={prepared.log_config}")

        # Offline mode has no client id / XUID, and some versions pass those two options
        # through as raw placeholders. Drop them (and any other leftover placeholder pair)
        # instead of handing Minecraft an empty value it would treat as an account id.
        online_only = {"--clientid", "--xuid"}
        cleaned: list[str] = []
        index = 0
        while index < len(game_arguments):
            argument = game_arguments[index]
            if argument.lower() in online_only:
                index += 2
                continue
            if "${" in argument:
                if cleaned and cleaned[-1].startswith("--"):
                    cleaned.pop()
                index += 1
                continue
            cleaned.append(argument)
            index += 1
        game_arguments = cleaned

        command: list[str] = [
            java_path,
            f"-Xms{memory_mb}M",
            f"-Xmx{memory_mb}M",
            "-Dfile.encoding=UTF-8",
            "-Dstdout.encoding=UTF-8",
            "-Dstderr.encoding=UTF-8",
            f"-Dminecraft.launcher.brand={APP_NAME}",
            f"-Dminecraft.launcher.version={APP_VERSION}",
        ]
        command.extend(extra_jvm or [])
        command.extend(jvm_arguments)
        command.append(prepared.main_class)
        command.extend(game_arguments)

        if fullscreen and "--fullscreen" not in game_arguments:
            command.append("--fullscreen")
        if resolution and "--width" not in game_arguments:
            command.extend(["--width", str(resolution[0]), "--height", str(resolution[1])])

        return command

    def launch(
        self,
        command: list[str],
        on_line: Callable[[str], None] | None = None,
    ) -> subprocess.Popen:
        self.game_dir.mkdir(parents=True, exist_ok=True)
        self.log("启动： " + subprocess.list2cmdline(command))
        try:
            process = subprocess.Popen(
                command,
                cwd=str(self.game_dir),
                stdout=subprocess.PIPE if on_line else None,
                stderr=subprocess.STDOUT if on_line else None,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            raise LauncherError(f"无法启动 Java 进程：{exc}") from exc

        if on_line and process.stdout is not None:
            def pump() -> None:
                assert process.stdout is not None
                for line in process.stdout:
                    on_line(line.rstrip("\r\n"))
                process.stdout.close()

            import threading

            threading.Thread(target=pump, daemon=True).start()
        return process
