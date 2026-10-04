"""Stahování webových stránek i přes firemní proxy s ověřováním Windows.

Python (urllib) sám neumí proxy s integrovaným ověřením (NTLM/Kerberos).
Proto se zkouší postupně několik cest a první úspěšná se zapamatuje:

1. WinINet (knihovna Windows, kterou používají Edge/IE a Office) přes ctypes –
   přebírá systémové nastavení proxy včetně PAC skriptu a přihlašuje se
   automaticky účtem přihlášeného uživatele.
2. PowerShell / .NET WebClient s DefaultNetworkCredentials – totéž jinou cestou.
3. urllib – mimo Windows nebo bez proxy.

Vše je ve standardní knihovně Pythonu, nic se neinstaluje.
"""

from __future__ import annotations

import os
import subprocess
import sys
import urllib.request

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HarmonogramLegislativa/1.0"


class FetchError(Exception):
    pass


class Fetcher:
    name = "?"

    def available(self) -> bool:
        return True

    def fetch(self, url: str, timeout: float) -> bytes:
        raise NotImplementedError


class WinInetFetcher(Fetcher):
    """Stažení přes WinINet – systémová proxy + automatické přihlášení Windows."""

    name = "Windows (WinINet)"
    INTERNET_OPEN_TYPE_PRECONFIG = 0
    INTERNET_FLAG_RELOAD = 0x80000000
    INTERNET_FLAG_NO_CACHE_WRITE = 0x04000000
    INTERNET_FLAG_NO_UI = 0x00000200
    INTERNET_FLAG_KEEP_CONNECTION = 0x00400000
    INTERNET_OPTION_CONNECT_TIMEOUT = 2
    INTERNET_OPTION_RECEIVE_TIMEOUT = 6
    INTERNET_OPTION_SEND_TIMEOUT = 5
    HTTP_QUERY_STATUS_CODE = 19
    HTTP_QUERY_FLAG_NUMBER = 0x20000000

    def available(self) -> bool:
        return sys.platform == "win32"

    def fetch(self, url: str, timeout: float) -> bytes:
        import ctypes
        from ctypes import wintypes

        wininet = ctypes.WinDLL("wininet", use_last_error=True)
        HINTERNET = ctypes.c_void_p
        wininet.InternetOpenW.restype = HINTERNET
        wininet.InternetOpenW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPCWSTR,
                                          wintypes.LPCWSTR, wintypes.DWORD]
        wininet.InternetOpenUrlW.restype = HINTERNET
        wininet.InternetOpenUrlW.argtypes = [HINTERNET, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                             wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
        wininet.InternetReadFile.restype = wintypes.BOOL
        wininet.InternetReadFile.argtypes = [HINTERNET, ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD)]
        wininet.InternetSetOptionW.restype = wintypes.BOOL
        wininet.InternetSetOptionW.argtypes = [HINTERNET, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        wininet.HttpQueryInfoW.restype = wintypes.BOOL
        wininet.HttpQueryInfoW.argtypes = [HINTERNET, wintypes.DWORD, ctypes.c_void_p,
                                           ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
        wininet.InternetCloseHandle.argtypes = [HINTERNET]

        session = wininet.InternetOpenW(USER_AGENT, self.INTERNET_OPEN_TYPE_PRECONFIG, None, None, 0)
        if not session:
            raise FetchError(f"InternetOpen selhal (chyba {ctypes.get_last_error()})")
        handle = None
        try:
            ms = wintypes.DWORD(int(timeout * 1000))
            for option in (self.INTERNET_OPTION_CONNECT_TIMEOUT, self.INTERNET_OPTION_RECEIVE_TIMEOUT,
                           self.INTERNET_OPTION_SEND_TIMEOUT):
                wininet.InternetSetOptionW(session, option, ctypes.byref(ms), ctypes.sizeof(ms))
            flags = (self.INTERNET_FLAG_RELOAD | self.INTERNET_FLAG_NO_CACHE_WRITE
                     | self.INTERNET_FLAG_NO_UI | self.INTERNET_FLAG_KEEP_CONNECTION)
            handle = wininet.InternetOpenUrlW(session, url, None, 0, flags, 0)
            if not handle:
                raise FetchError(f"InternetOpenUrl selhal (chyba {ctypes.get_last_error()})")

            status = wintypes.DWORD(0)
            size = wintypes.DWORD(ctypes.sizeof(status))
            if wininet.HttpQueryInfoW(handle, self.HTTP_QUERY_STATUS_CODE | self.HTTP_QUERY_FLAG_NUMBER,
                                      ctypes.byref(status), ctypes.byref(size), None):
                if status.value == 407:
                    raise FetchError("proxy odmítla přihlášení (HTTP 407)")
                if status.value >= 400:
                    raise FetchError(f"HTTP {status.value}")

            chunks = []
            buffer = ctypes.create_string_buffer(65536)
            read = wintypes.DWORD(0)
            while True:
                if not wininet.InternetReadFile(handle, buffer, len(buffer), ctypes.byref(read)):
                    raise FetchError(f"InternetReadFile selhal (chyba {ctypes.get_last_error()})")
                if read.value == 0:
                    break
                chunks.append(buffer.raw[:read.value])
            return b"".join(chunks)
        finally:
            if handle:
                wininet.InternetCloseHandle(handle)
            wininet.InternetCloseHandle(session)


class PowerShellFetcher(Fetcher):
    """Záloha: .NET WebClient se systémovou proxy a přihlášením Windows."""

    name = "Windows (PowerShell)"
    SCRIPT = (
        "$ErrorActionPreference='Stop';"
        "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;"
        "$wc=New-Object System.Net.WebClient;"
        "$wc.Proxy=[System.Net.WebRequest]::GetSystemWebProxy();"
        "$wc.Proxy.Credentials=[System.Net.CredentialCache]::DefaultNetworkCredentials;"
        "$wc.UseDefaultCredentials=$true;"
        "$wc.Headers.Add('User-Agent',$env:HARMONOGRAM_UA);"
        "$b=$wc.DownloadData($env:HARMONOGRAM_URL);"
        "$o=[Console]::OpenStandardOutput();$o.Write($b,0,$b.Length);$o.Flush()"
    )

    def available(self) -> bool:
        return sys.platform == "win32"

    def fetch(self, url: str, timeout: float) -> bytes:
        env = dict(os.environ, HARMONOGRAM_URL=url, HARMONOGRAM_UA=USER_AGENT)
        try:
            done = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-Command", self.SCRIPT],
                capture_output=True, env=env, timeout=timeout + 15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FetchError(str(exc)) from exc
        if done.returncode != 0 or not done.stdout:
            message = done.stderr.decode("utf-8", errors="replace").strip().splitlines()
            raise FetchError(message[0] if message else f"návratový kód {done.returncode}")
        return done.stdout


class UrllibFetcher(Fetcher):
    name = "Python (urllib)"

    def fetch(self, url: str, timeout: float) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as exc:
            raise FetchError(str(exc)) from exc


class ResilientDownloader:
    """Zkouší stahovací metody popořadě; úspěšnou použije příště jako první."""

    def __init__(self, fetchers: list[Fetcher] | None = None, timeout: float = 15):
        fetchers = fetchers or [WinInetFetcher(), PowerShellFetcher(), UrllibFetcher()]
        self.fetchers = [f for f in fetchers if f.available()]
        self.timeout = timeout
        self.last_method: str | None = None

    def fetch(self, url: str) -> bytes:
        errors = []
        for fetcher in list(self.fetchers):
            try:
                data = fetcher.fetch(url, self.timeout)
            except Exception as exc:
                errors.append(f"{fetcher.name}: {exc}")
                continue
            # úspěšnou metodu dát na první místo
            self.fetchers.remove(fetcher)
            self.fetchers.insert(0, fetcher)
            self.last_method = fetcher.name
            return data
        raise FetchError(" | ".join(errors))
