"""Microsoft / Xbox Live account support for CppMc.

Standard third party launcher chain:

    device code -> Microsoft token -> Xbox Live -> XSTS -> Minecraft -> profile

Logging in never touches the user's browser for us: we print a code, the user
opens the page themselves and enters it. Tokens are cached in
``%APPDATA%\\CppMc\\account.json`` and refreshed silently; offline mode keeps
working when there is no account at all.

Only the Python standard library is used.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from .core import USER_AGENT, LauncherError, app_dir

# Public device-code registration used by several open source launchers. If it
# ever gets revoked, point CPMC_MS_CLIENT_ID at your own Azure app registration
# (type: public client, scopes: XboxLive.signin + offline_access).
DEFAULT_CLIENT_ID = "c36a9fb6-4f2a-41ff-90bd-ae7cc92031eb"
CLIENT_ID = os.environ.get("CPMC_MS_CLIENT_ID", DEFAULT_CLIENT_ID)
SCOPE = "XboxLive.signin offline_access"
DEVICE_CODE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
XBL_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
MC_PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"
MC_ENTITLEMENTS_URL = "https://api.minecraftservices.com/entitlements/mcstore"

LogFn = Callable[[str], None]
CodeFn = Callable[[str, str], None]

ACCOUNT_PATH = app_dir() / "account.json"


# --------------------------------------------------------------------------- #
# http helpers
# --------------------------------------------------------------------------- #

def _post_json(url: str, payload: dict, bearer: str = "") -> dict:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": USER_AGENT}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    return _read_json(request)


def _post_form(url: str, fields: dict[str, str]) -> dict:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    return _read_json(request)


def _get_json(url: str, bearer: str = "") -> dict:
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    return _read_json(urllib.request.Request(url, headers=headers))


def _read_json(request: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:400]
        except Exception:  # noqa: BLE001
            pass
        raise LauncherError(f"微软登录接口返回 {exc.code}：{detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise LauncherError(f"网络错误：{exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise LauncherError(f"登录接口返回的数据无法解析：{exc}") from exc


# --------------------------------------------------------------------------- #
# device code login
# --------------------------------------------------------------------------- #

def request_device_code() -> dict:
    data = _post_form(DEVICE_CODE_URL, {"client_id": CLIENT_ID, "scope": SCOPE})
    if not data.get("device_code"):
        raise LauncherError("微软没有返回设备码，登录无法继续")
    return data


def wait_for_token(device: dict, on_code: CodeFn | None = None,
                   on_status: LogFn | None = None, timeout: float | None = None) -> dict:
    """Poll the token endpoint until the user finishes signing in."""
    interval = float(device.get("interval") or 5)
    deadline = time.time() + (timeout if timeout is not None else float(device.get("expires_in") or 900))

    if on_code:
        on_code(str(device.get("user_code") or ""), str(device.get("verification_uri") or ""))

    while time.time() < deadline:
        time.sleep(interval)
        try:
            data = _post_form(TOKEN_URL, {
                "client_id": CLIENT_ID,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device["device_code"],
            })
        except LauncherError as exc:
            message = str(exc)
            if "authorization_pending" in message:
                continue
            if "slow_down" in message:
                interval += 5
                continue
            if "expired_token" in message or "code_expired" in message:
                raise LauncherError("设备码已过期，请重新发起登录") from exc
            raise
        if data.get("access_token"):
            return data
        if on_status:
            on_status("还在等待授权…")
    raise LauncherError("等待登录超时，请重新发起登录")


def refresh_tokens(refresh_token: str) -> dict:
    data = _post_form(TOKEN_URL, {
        "client_id": CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": SCOPE,
    })
    if not data.get("access_token"):
        raise LauncherError("令牌刷新失败，需要重新登录")
    return data


# --------------------------------------------------------------------------- #
# microsoft token -> minecraft profile
# --------------------------------------------------------------------------- #

def microsoft_to_minecraft(ms_token: str, log: LogFn | None = None) -> dict:
    log = log or (lambda message: None)
    log("正在通过 Xbox Live 验证…")
    xbl = _post_json(XBL_URL, {
        "Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com",
                       "RpsTicket": f"d={ms_token}"},
        "RelyingParty": "http://auth.xboxlive.com",
        "TokenType": "JWT",
    })
    xbl_token = xbl.get("Token")

    log("正在获取 XSTS 令牌…")
    xsts = _post_json(XSTS_URL, {
        "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl_token]},
        "RelyingParty": "rp://api.minecraftservices.com/",
        "TokenType": "JWT",
    })
    if xsts.get("XErr"):
        code = xsts["XErr"]
        hints = {
            2148916233: "这个微软账号还没有 Xbox 账号，请先去 xbox.com 创建",
            2148916235: "该地区不支持 Xbox Live",
            2148916238: "这是未成年账号，需要家长加入家庭组后才能登录",
        }
        raise LauncherError(f"Xbox 验证失败（{code}）：{hints.get(code, '请稍后再试')}")

    uhs = ((xsts.get("DisplayClaims") or {}).get("xui") or [{}])[0].get("uhs", "")
    token = xsts.get("Token")

    log("正在换取 Minecraft 访问令牌…")
    mc = _post_json(MC_LOGIN_URL, {"identityToken": f"XBL3.0 x={uhs};{token}"})
    access_token = mc.get("access_token")
    if not access_token:
        raise LauncherError("Minecraft 拒绝了这次登录，账号可能没有购买 Java 版")

    profile = _get_json(MC_PROFILE_URL, bearer=access_token)
    if not profile.get("id"):
        raise LauncherError("该账号没有 Minecraft Java 版档案（未购买或未创建角色名）")

    xuid = ((xbl.get("DisplayClaims") or {}).get("xui") or [{}])[0].get("xid", "")
    return {
        "name": profile.get("name", ""),
        "uuid": profile.get("id", ""),
        "access_token": access_token,
        "xuid": xuid,
    }


def check_entitlement(access_token: str) -> bool:
    """True when the account owns Minecraft: Java Edition."""
    try:
        data = _get_json(MC_ENTITLEMENTS_URL, bearer=access_token)
    except LauncherError:
        return False
    return bool(data.get("items"))


# --------------------------------------------------------------------------- #
# token storage
# --------------------------------------------------------------------------- #

def load_account() -> dict | None:
    try:
        data = json.loads(ACCOUNT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("access_token"):
        return None
    if not data.get("uuid") or not data.get("name"):
        return None
    return data


def save_account(account: dict) -> None:
    ACCOUNT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACCOUNT_PATH.write_text(json.dumps(account, indent=2), encoding="utf-8")
    if os.name != "nt":
        try:
            ACCOUNT_PATH.chmod(0o600)
        except OSError:
            pass


def clear_account() -> None:
    try:
        ACCOUNT_PATH.unlink()
    except OSError:
        pass


def login(on_code: CodeFn | None = None, log: LogFn | None = None) -> dict:
    """Interactive device code login; returns the stored account."""
    log = log or (lambda message: None)
    device = request_device_code()
    tokens = wait_for_token(device, on_code=on_code, on_status=log)
    profile = microsoft_to_minecraft(tokens["access_token"], log)
    account = {
        **profile,
        "refresh_token": tokens.get("refresh_token", ""),
        "expires_at": time.time() + int(tokens.get("expires_in") or 3600),
        "ms_access_token": tokens.get("access_token", ""),
        "logged_in_at": time.time(),
    }
    save_account(account)
    log(f"登录成功：{account['name']}")
    return account


def ensure_account(log: LogFn | None = None, refresh_window: int = 300) -> dict | None:
    """Return a usable account, refreshing the token when it is close to expiring."""
    log = log or (lambda message: None)
    account = load_account()
    if not account:
        return None
    if float(account.get("expires_at") or 0) - time.time() > refresh_window:
        return account
    refresh_token = account.get("refresh_token")
    if not refresh_token:
        log("账号令牌已过期，需要重新登录")
        return None
    log("正在刷新微软账号令牌…")
    try:
        tokens = refresh_tokens(refresh_token)
        profile = microsoft_to_minecraft(tokens["access_token"], log)
    except LauncherError as exc:
        log(f"刷新令牌失败：{exc}")
        return None
    refreshed = {
        **account,
        **profile,
        "refresh_token": tokens.get("refresh_token", refresh_token),
        "expires_at": time.time() + int(tokens.get("expires_in") or 3600),
        "ms_access_token": tokens.get("access_token", ""),
    }
    save_account(refreshed)
    return refreshed


# --------------------------------------------------------------------------- #
# command patching
# --------------------------------------------------------------------------- #

AUTH_FLAGS = {
    "--username": "name",
    "--uuid": "uuid",
    "--accessToken": "access_token",
    "--userType": "user_type",
}


def apply_account(command: list[str], account: dict) -> list[str]:
    """Rewrite the offline auth arguments of a built command to use a real account."""
    values = {
        "name": account.get("name", ""),
        "uuid": account.get("uuid", ""),
        "access_token": account.get("access_token", ""),
        "user_type": "msa",
    }
    patched = list(command)
    for index in range(len(patched) - 1):
        flag = patched[index]
        key = AUTH_FLAGS.get(flag)
        if key and values[key]:
            patched[index + 1] = values[key]
    return patched
