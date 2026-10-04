"""Desktopová část: malé okno, které drží server a na kliknutí otevře prohlížeč."""

from __future__ import annotations

import webbrowser

from .server import HarmonogramServer


class DesktopWindow:
    """Okno v tkinteru se stavem serveru a tlačítky."""

    TITLE = "Harmonogram legislativního procesu"

    def __init__(self, server: HarmonogramServer):
        import tkinter as tk
        from tkinter import ttk

        self.server = server
        self.tk = tk
        self.root = tk.Tk()
        self.root.title(self.TITLE)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

        frame = ttk.Frame(self.root, padding=18)
        frame.grid()
        ttk.Label(frame, text=self.TITLE, font=("Segoe UI", 13, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(frame, text="Server běží na adrese:").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(10, 2))

        self.url_var = tk.StringVar(value=server.url)
        entry = ttk.Entry(frame, textvariable=self.url_var, width=34, state="readonly")
        entry.grid(row=2, column=0, columnspan=3, sticky="we")

        ttk.Button(frame, text="Otevřít v prohlížeči", command=self.open_browser).grid(
            row=3, column=0, pady=(14, 0), sticky="we")
        ttk.Button(frame, text="Kopírovat adresu", command=self.copy_url).grid(
            row=3, column=1, pady=(14, 0), padx=8, sticky="we")
        ttk.Button(frame, text="Ukončit", command=self.quit).grid(
            row=3, column=2, pady=(14, 0), sticky="we")

        self.status_var = tk.StringVar(value="Zavřením okna se server ukončí.")
        ttk.Label(frame, textvariable=self.status_var, foreground="#666").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))

    def open_browser(self) -> None:
        webbrowser.open(self.server.url)
        self.status_var.set("Prohlížeč otevřen.")

    def copy_url(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.server.url)
        self.status_var.set("Adresa zkopírována do schránky.")

    def quit(self) -> None:
        self.server.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


class ConsoleRunner:
    """Náhradní režim bez grafiky (např. chybí tkinter)."""

    def __init__(self, server: HarmonogramServer):
        self.server = server

    def run(self) -> None:
        print(f"Harmonogram běží na {self.server.url}")
        print("Enter = otevřít prohlížeč, q + Enter = ukončit")
        try:
            while True:
                command = input("> ").strip().lower()
                if command in ("q", "quit", "konec"):
                    break
                webbrowser.open(self.server.url)
        except (EOFError, KeyboardInterrupt):
            pass
        finally:
            self.server.stop()


class Application:
    """Vstupní bod: spustí server a zvolené uživatelské rozhraní."""

    def __init__(self, port: int = HarmonogramServer.DEFAULT_PORT, console: bool = False):
        self.server = HarmonogramServer(port=port)
        self.console = console

    def run(self) -> None:
        self.server.start()
        if not self.console:
            try:
                DesktopWindow(self.server).run()
                return
            except Exception as exc:  # tkinter chybí nebo není displej
                print(f"Grafické okno nelze otevřít ({exc}); přepínám do konzole.")
        ConsoleRunner(self.server).run()


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Harmonogram legislativního procesu")
    parser.add_argument("--port", type=int, default=HarmonogramServer.DEFAULT_PORT,
                        help="preferovaný port (výchozí 8421)")
    parser.add_argument("--console", action="store_true", help="bez grafického okna")
    parser.add_argument("--test-download", action="store_true",
                        help="vyzkoušet stažení psp.cz a senat.cz všemi metodami a vypsat výsledek")
    args = parser.parse_args(argv)
    if args.test_download:
        test_download()
        return
    Application(args.port, args.console).run()


def test_download() -> None:
    """Diagnostika sítě: každou metodu zkusí zvlášť (spouštět přes python, ne pythonw)."""
    from .net import PowerShellFetcher, UrllibFetcher, WinInetFetcher
    urls = ["https://www.psp.cz/sqw/hp.sqw?k=200", "https://www.senat.cz/cinnost/harmonogram/index.php"]
    for fetcher in (WinInetFetcher(), PowerShellFetcher(), UrllibFetcher()):
        if not fetcher.available():
            print(f"{fetcher.name}: na tomto systému není k dispozici")
            continue
        for url in urls:
            try:
                size = len(fetcher.fetch(url, 15))
                print(f"OK    {fetcher.name}: {url} ({size} B)")
            except Exception as exc:
                print(f"CHYBA {fetcher.name}: {url} – {exc}")
