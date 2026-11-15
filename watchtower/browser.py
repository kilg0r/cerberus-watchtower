"""Open URLs in the user's default browser (not the window Watchtower is in).

The launcher shows Watchtower as a chromeless Edge app window, where every
`target=_blank` link opens in Edge. To land in the default browser (Chrome on
this machine) the server has to launch it - it runs in the logged-on user's
session (logon task / launcher), so ShellExecute opens a normal tab there.
"""

import os
import sys
import webbrowser

# UrlAssociations\https\UserChoice ProgId -> display name
_PROG_IDS = {
    "chromehtml": "Chrome",
    "msedgehtm": "Edge",
    "bravehtml": "Brave",
    "vivaldihtm": "Vivaldi",
    "firefoxurl": "Firefox",
    "operastable": "Opera",
    "operagxstable": "Opera GX",
}

_USER_CHOICE = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"


def browser_name(prog_id: str | None) -> str | None:
    """Display name for a UserChoice ProgId. Firefox/Opera suffix theirs
    (`FirefoxURL-308046B0AF4A39CB`), so match on the prefix."""
    if not prog_id:
        return None
    key = prog_id.lower()
    for prefix, name in _PROG_IDS.items():
        if key == prefix or key.startswith(prefix + "-"):
            return name
    return None


def default_browser() -> str | None:
    """Name of the default https handler, or None when unknown/not Windows."""
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _USER_CHOICE) as key:
            prog_id, _ = winreg.QueryValueEx(key, "ProgId")
    except OSError:
        return None
    return browser_name(prog_id)


def open_url(url: str) -> None:
    """Hand an http(s) URL to the default browser. Raises ValueError for
    anything else - this must never become a way to launch files."""
    if not url.startswith(("https://", "http://")):
        raise ValueError(f"refusing to open non-http url: {url!r}")
    if sys.platform == "win32":
        os.startfile(url)  # ShellExecute "open" -> default browser
    elif not webbrowser.open(url):
        raise OSError("no browser available")
