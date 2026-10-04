# Harmonogram legislativního procesu

Desktopová aplikace v Pythonu (jen standardní knihovna, bez instalace balíčků).
Spustí lokální webový server a okno s tlačítkem **Otevřít v prohlížeči** –
prohlížeč se sám neotevírá.

## Spuštění

```
python main.pyw            # okno + server na http://127.0.0.1:8421/
python main.pyw --port 9000
python main.pyw --console  # bez okna (když chybí tkinter)
```

Vyžaduje Python 3.10+. Je-li port obsazený, použije se nejbližší volný (8765 se přeskakuje).

## Ovládání

- Každý řádek = fáze. Ve sloupci **Lhůta** je, za kolik dní (kalendářních / pracovních)
  nejdříve – nebo u Senátu a prezidenta nejpozději – fáze následuje po uvedené fázi.
- **Přepsat** – zatržítko zobrazí pole pro vlastní datum; další fáze se počítají od něj.
  Datum se potvrdí klávesou Enter nebo opuštěním pole (během psaní se nic nepřepočítává);
  nedopsané datum se vrátí na původní hodnotu. Datum, které porušuje lhůty, se označí (žlutě nastavená lhůta, červeně zákonný limit).
- Značka **§ min 10** / **§ max 30** / **§ max 15** = nepřekročitelný limit; lhůtu nelze nastavit pod/nad něj.
- **První krok harmonogramu** (výběr nahoře) – harmonogram může začít kteroukoli fází, např.
  předáním do PSP; dřívější fáze se skryjí a nejdou ani do Wordu. „Datum prvního kroku“ je jeho datum.
- Nahoře dále: globální dny v týdnu (odeslání na poradu, porada, vláda)
  a posun konce lhůty z víkendu/svátku na pracovní den.
- Volitelné fáze (2. porada, PSP po Senátu, PSP po prezidentovi) se zapínají zatržítkem u fáze.
- **Export Word** vytvoří harmonogram podle šablony `harmonogram/templates/harmonogram_sablona.docx`
  (oddíly VPŘ, MPŘ, Vláda, PS, Senát, Prezident, Sbírka zákonů; sloupce PROCES / TERMÍN / POZNÁMKA).
  Název návrhu a účinnost se zadávají nahoře, poznámky ke každému řádku v rozbalovacím poli „Word: …“
  u fáze (výchozí poznámky se samy přizpůsobují nastaveným lhůtám). Šablonu lze nahradit vlastní
  se stejnou stavbou (nadpisy stylu „nadpis zákona“, jedna tabulka o 4 sloupcích s řádkem záhlaví).
- **Harmonogramy PSP a Senátu** se při spuštění stáhnou z psp.cz a senat.cz (pokud jsou starší než
  týden; ručně tlačítkem „Aktualizovat“) a uloží do `~/.legislativni_harmonogram/parlament.json`.
  Schůze PSP (1.–3. čtení, projednání po vrácení) se plánují na út–pá v týdnech „Poslanecká sněmovna“,
  případně ve variabilních týdnech (přepínač); 3. čtení jen ve středu nebo v pátek. Senát se plánuje
  na poslední plenární den v lhůtě 30 dnů; když v lhůtě žádný není, nastane fikce schválení.
  „Ignorovat zvyklosti“ u fáze dovolí jakýkoli pracovní týden. Týdny mimo zveřejněný harmonogram
  se neomezují. Když se stránku nepodaří stáhnout nebo rozpoznat, lze ji uložit z prohlížeče
  a načíst tlačítkem „Načíst uloženou stránku…“, případně opravit typ týdne ručně v přehledu týdnů
  (PSP: řádná / mimořádná / žádná schůze, Senát: plenární / žádná; barevně odlišeno, ručně
  nastavené týdny mají přerušovaný rámeček; ručně zadaná mimořádná schůze platí vždy).
- **Firemní proxy s ověřováním Windows** (NTLM/Kerberos): stránky se stahují přes WinINet
  (stejná knihovna jako Edge/Office – systémové nastavení proxy, přihlášení účtem Windows),
  záložně přes PowerShell/.NET a nakonec přes Python. Funkční metodu ukáže stav harmonogramu.
  Diagnostika: `python main.pyw --test-download` vypíše výsledek každé metody.
- **Varianty** (lišta nahoře): „+ Nová varianta“ vytvoří kopii právě naklikaného harmonogramu,
  pole „Název“ ji přejmenuje, „Smazat variantu“ ji zruší, kliknutím na záložku se přepíná.
  Varianty se ukládají do `~/.legislativni_harmonogram/varianty.json`.
  **Uložit** stáhne celý harmonogram se všemi variantami do jednoho JSON souboru, **Načíst**
  ho celý obnoví (současné varianty nahradí; zvládne i starší soubory s jednou variantou).
  **Nový harmonogram** smaže všechny varianty a začne načisto.
  **Export Word** při více variantách vytvoří místo sloupce TERMÍN sloupec pro každou variantu
  (v záhlaví její název; fáze, která ve variantě není, má „—“; poznámky z právě otevřené varianty).
- **Orientační termíny**: schůze komory mimo zveřejněný harmonogram (a vše, co na ní závisí)
  se počítají s konkrétním dnem, ale ve Wordu, CSV i v tabulce se uvádějí jen měsícem a rokem
  („říjen 2027“). Ručně zadané datum je vždy přesné.
- **Prázdniny**: bez údajů z harmonogramu se předpokládá, že PSP v červenci a srpnu nezasedá
  (Senát se neomezuje); zveřejněný harmonogram nebo ruční nastavení týdne mají přednost.
- **Export CSV** pro Excel.

## Počítání lhůt

Den události se nezapočítává: lhůta 10 dnů od 1. běží 2.–11., další úkon nejdříve 12.
Úkony se plánují jen na pracovní dny; svátky podle zákona č. 245/2000 Sb. včetně
Velkého pátku a Velikonočního pondělí (výpočet Velikonoc algoritmicky).
Připadne-li den porady / vlády na svátek, použije se nejbližší další pracovní den.

## Struktura

| Soubor | Třídy |
|---|---|
| `harmonogram/calendar_cz.py` | `CzechHolidayCalendar` – svátky, Velikonoce, pracovní dny |
| `harmonogram/deadlines.py` | `DayUnit`, `DeadlineCalculator` – právnické počítání lhůt |
| `harmonogram/model.py` | `Constraint`, `Phase`, `OptionalGroup`, `ScheduleSettings`, `LegislativePlan`, `PlanStateMapper` |
| `harmonogram/process.py` | `DefaultProcessFactory` – výchozí fáze a lhůty (zde upravujte proces) |
| `harmonogram/scheduler.py` | `ScheduleCalculator`, `Bounds`, `PhaseResult`, `ScheduleSerializer` |
| `harmonogram/docx_export.py` | `DocxHarmonogramExporter`, `DocRowsBuilder`, `CzechDateFormatter` – export do Wordu |
| `harmonogram/parliament.py` | `HarmonogramPageParser`, `ParliamentCalendarStore`, `SittingPolicy`, `WeekType` – týdny zasedání komor |
| `harmonogram/net.py` | `WinInetFetcher`, `PowerShellFetcher`, `UrllibFetcher`, `ResilientDownloader` – stahování přes proxy |
| `harmonogram/variants.py` | `VariantStore` – uložení variant harmonogramu |
| `harmonogram/server.py` | `HarmonogramService`, `HarmonogramRequestHandler`, `HarmonogramServer` |
| `harmonogram/app.py` | `DesktopWindow` (tkinter), `ConsoleRunner`, `Application` |
| `harmonogram/static/` | webové rozhraní (HTML/CSS/JS) |

Testy: `python -m unittest discover -s tests -t .`

Samostatný .exe (volitelně): `pyinstaller --onefile --windowed --add-data "harmonogram/static;harmonogram/static" --add-data "harmonogram/templates;harmonogram/templates" main.pyw`
