"""Live system controls shown inline in the launcher: volume, mute, screen brightness, dark mode.

A settings result can carry controls in its widget, e.g. "my screen is too dim" -> a brightness slider right
in the results, so a small change never needs the Settings app. Every call is cheap (< 10 ms after the first)
and runs on whichever server thread asks, so each one initialises COM for its own thread.

  controls_for(category) -> [{id, kind: "slider"|"toggle", label, icon, value, min?, max?, unit?}]
  set_control(id, value) -> the control's new state (same shape), or raises KeyError / OSError
"""
from __future__ import annotations

import ctypes
import logging
import winreg
from ctypes import HRESULT, POINTER, c_float, c_uint, c_void_p
from ctypes.wintypes import BOOL, DWORD, LPCWSTR

import comtypes
import comtypes.client
from comtypes import COMMETHOD, GUID, IUnknown

log = logging.getLogger(__name__)


# ---- volume (Core Audio, default output device) --------------------------------------------------------
class IAudioEndpointVolume(IUnknown):
    _iid_ = GUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")
    _methods_ = [
        COMMETHOD([], HRESULT, "RegisterControlChangeNotify", (["in"], c_void_p, "pNotify")),
        COMMETHOD([], HRESULT, "UnregisterControlChangeNotify", (["in"], c_void_p, "pNotify")),
        COMMETHOD([], HRESULT, "GetChannelCount", (["out"], POINTER(c_uint), "pnChannelCount")),
        COMMETHOD([], HRESULT, "SetMasterVolumeLevel", (["in"], c_float, "fLevelDB"), (["in"], POINTER(GUID), "ctx")),
        COMMETHOD([], HRESULT, "SetMasterVolumeLevelScalar", (["in"], c_float, "fLevel"), (["in"], POINTER(GUID), "ctx")),
        COMMETHOD([], HRESULT, "GetMasterVolumeLevel", (["out"], POINTER(c_float), "pfLevelDB")),
        COMMETHOD([], HRESULT, "GetMasterVolumeLevelScalar", (["out"], POINTER(c_float), "pfLevel")),
        COMMETHOD([], HRESULT, "SetChannelVolumeLevel", (["in"], c_uint, "n"), (["in"], c_float, "db"), (["in"], POINTER(GUID), "ctx")),
        COMMETHOD([], HRESULT, "SetChannelVolumeLevelScalar", (["in"], c_uint, "n"), (["in"], c_float, "v"), (["in"], POINTER(GUID), "ctx")),
        COMMETHOD([], HRESULT, "GetChannelVolumeLevel", (["in"], c_uint, "n"), (["out"], POINTER(c_float), "db")),
        COMMETHOD([], HRESULT, "GetChannelVolumeLevelScalar", (["in"], c_uint, "n"), (["out"], POINTER(c_float), "v")),
        COMMETHOD([], HRESULT, "SetMute", (["in"], BOOL, "bMute"), (["in"], POINTER(GUID), "ctx")),
        COMMETHOD([], HRESULT, "GetMute", (["out"], POINTER(BOOL), "pbMute")),
    ]


class IMMDevice(IUnknown):
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Activate", (["in"], POINTER(GUID), "iid"), (["in"], DWORD, "dwClsCtx"),
                  (["in"], c_void_p, "pActivationParams"), (["out"], POINTER(POINTER(IUnknown)), "ppInterface")),
    ]


class IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = [
        COMMETHOD([], HRESULT, "EnumAudioEndpoints", (["in"], DWORD, "dataFlow"), (["in"], DWORD, "mask"),
                  (["out"], POINTER(c_void_p), "ppDevices")),
        COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint", (["in"], DWORD, "dataFlow"), (["in"], DWORD, "role"),
                  (["out"], POINTER(POINTER(IMMDevice)), "ppEndpoint")),
    ]


CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
E_RENDER, E_MULTIMEDIA, CLSCTX_ALL = 0, 1, 23


def _endpoint() -> IAudioEndpointVolume:
    comtypes.CoInitialize()
    en = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER)
    dev = en.GetDefaultAudioEndpoint(E_RENDER, E_MULTIMEDIA)
    return dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None).QueryInterface(IAudioEndpointVolume)


def get_volume() -> int:
    return round(_endpoint().GetMasterVolumeLevelScalar() * 100)


def set_volume(v: float) -> int:
    ep = _endpoint()
    ep.SetMasterVolumeLevelScalar(max(0.0, min(1.0, float(v) / 100)), None)
    if v > 0 and ep.GetMute():
        ep.SetMute(False, None)       # dragging the slider up should be heard
    return round(ep.GetMasterVolumeLevelScalar() * 100)


def get_mute() -> bool:
    return bool(_endpoint().GetMute())


def set_mute(on) -> bool:
    ep = _endpoint()
    ep.SetMute(bool(on), None)
    return bool(ep.GetMute())


# ---- brightness (WMI; built-in laptop / tablet panels, not external monitors) ----------------------------
def _wmi():
    comtypes.CoInitialize()
    return comtypes.client.CoGetObject(r"winmgmts:root\wmi")


def get_brightness() -> int | None:
    try:
        for o in _wmi().ExecQuery("SELECT CurrentBrightness FROM WmiMonitorBrightness"):
            return int(o.Properties_("CurrentBrightness").Value)
    except OSError:                    # no WMI brightness: a desktop with an external monitor
        return None
    return None


def set_brightness(v: float) -> int:
    v = int(max(0, min(100, round(float(v)))))
    for o in _wmi().ExecQuery("SELECT * FROM WmiMonitorBrightnessMethods"):
        args = o.Methods_("WmiSetBrightness").InParameters.SpawnInstance_()
        args.Properties_("Timeout").Value = 0
        args.Properties_("Brightness").Value = v
        o.ExecMethod_("WmiSetBrightness", args)
    return v


# ---- dark mode (per-user theme; apps and the taskbar follow it live) -------------------------------------
_THEME = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


def get_dark() -> bool:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _THEME) as k:
        return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0


def set_dark(on) -> bool:
    light = 0 if on else 1
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _THEME, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "AppsUseLightTheme", 0, winreg.REG_DWORD, light)
        winreg.SetValueEx(k, "SystemUsesLightTheme", 0, winreg.REG_DWORD, light)
    # tell running windows (taskbar, Explorer, apps) to re-read the theme
    send = ctypes.windll.user32.SendMessageTimeoutW
    send.argtypes = [c_void_p, c_uint, c_void_p, LPCWSTR, c_uint, c_uint, POINTER(c_void_p)]
    send(c_void_p(0xFFFF), 0x001A, None, "ImmersiveColorSet", 0x0002, 200, None)
    return get_dark()


# ---- registry of controls ---------------------------------------------------------------------------
CONTROLS = {
    "volume": dict(kind="slider", label="Volume", icon="🔊", min=0, max=100, unit="%", get=get_volume, set=set_volume),
    "mute": dict(kind="toggle", label="Mute", icon="🔇", get=get_mute, set=set_mute),
    "brightness": dict(kind="slider", label="Brightness", icon="☀️", min=0, max=100, unit="%",
                       get=get_brightness, set=set_brightness),
    "dark": dict(kind="toggle", label="Dark mode", icon="🌙", get=get_dark, set=set_dark),
}
# which controls a settings category shows (the category of the top settings result)
BY_CATEGORY = {"sound": ["volume", "mute"], "display": ["brightness", "dark"], "personalization": ["dark"]}


def _state(cid: str, value) -> dict:
    c = CONTROLS[cid]
    return {"id": cid, "value": value, **{k: v for k, v in c.items() if k not in ("get", "set")}}


def controls_for(category: str | None) -> list[dict]:
    out = []
    for cid in BY_CATEGORY.get(category or "", []):
        try:
            v = CONTROLS[cid]["get"]()
        except Exception:  # noqa: BLE001 — a control this PC can't read is simply not shown
            log.debug("control %s unavailable", cid, exc_info=True)
            continue
        if v is not None:
            out.append(_state(cid, v))
    return out


def set_control(cid: str, value) -> dict:
    if cid not in CONTROLS:
        raise KeyError(cid)
    return _state(cid, CONTROLS[cid]["set"](value))
