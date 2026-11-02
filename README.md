# PlasmaNest

Manuell nesting av G-kod för LinuxCNC/QtPlasmaC. Läs in en eller flera G-kodsfiler, placera
figurerna på en stålplåt och exportera en enda `.ngc`-fil som QtPlasmaC kan köra.

## Installation

Kräver Python 3.7 eller senare och PyQt5.

**Windows 11**

```bash
python -m pip install -r requirements.txt
```

Starta med `start.bat`, eller med `python plasmanest.py`.

**Debian** (samma paket som QtPlasmaC själv använder)

```bash
sudo apt install python3-pyqt5
```

```bash
sh start.sh
```

Testa gärna med filerna i `samples/`.

## Användning

| Vad | Hur |
|---|---|
| Läsa in G-kod | **Läs in…** eller Ctrl+L. Figuren läggs på en ledig plats på plåten. |
| Plåtens storlek | Bredd och höjd i panelen. Plåtens nedre vänstra hörn är G-kodens X0 Y0. |
| Flytta | Dra med vänster musknapp, skriv X/Y i panelen, eller använd piltangenterna (1 mm, med Shift 10 mm). |
| Rotera | Dra i den gröna ringen (Ctrl ger steg om 15°), Shift+mushjul, tangenten R, knapparna i panelen, eller skriv vinkeln. |
| Markera flera | Dra en ruta på tom yta, eller Ctrl-klicka. Flera figurer roterar som en grupp. |
| Kopiera | **Kopiera…** eller Ctrl+D, och ange antal. Kopiorna läggs på lediga platser. |
| Ta bort | **Ta bort** (markerade figurer) eller **Ta bort hela G-koden** (alla kopior av filen). |
| Skärordning | Listan visar ordningen. Ändra med **Skär tidigare** och **Skär senare**. |
| Ångra | Ctrl+Z. Gör om med Ctrl+Y (Linux: Ctrl+Shift+Z). |
| Vy | Mushjul zoomar, höger- eller mittenknapp panorerar, F anpassar till fönstret. |

Figurer som ligger utanför plåten blir **röda** och figurer som överlappar blir **orange**. Att
lägga en figur inuti ett hål i en annan räknas inte som överlapp. Kontrollen gäller den
programmerade banan, så skärspalten (kerf) räknas inte med: lämna själv tillräckligt avstånd.

## Två sorters filer

- **Exportera till QtPlasmaC (.ngc)**, Ctrl+E: filen du öppnar i QtPlasmaC och skär med.
- **Spara projekt (.pnest)**, Ctrl+S: din placering, för att kunna fortsätta arbeta senare. Filen
  innehåller även G-koden, så den fungerar även om originalfilerna flyttas. QtPlasmaC kan inte läsa den.

## Vad exporten gör med G-koden

- Alla koordinater räknas om till sina slutliga värden, och båg-centrum (I/J) roteras med.
- Filen blir alltid i millimeter med ett enda programslut (`M2`). Tum-filer räknas om till mm,
  inklusive numeriska F-värden.
- R-bågar skrivs om till I/J. `F#<_hal[plasmac.cut-feed-rate]>`, `M3 $n`, `M5 $n`, `M67`, `M68`,
  `G41.1`/`G42.1` och kommentarer behålls.
- `M190`/`M66` tas bara med när materialet faktiskt ändras. Materialkommentarer `(o=1…)` och
  `(o=2…)` flyttas först i filen, eftersom QtPlasmaC kräver det.
- Rörelser efter sista skärningen (t.ex. parkering) tas bort från varje figur.

## G-kod som inte går att läsa in

Filer som inte kan flyttas säkert vägras med radnummer, i stället för att ge fel skärning:

- `G92`, `G52` och `G10` (förskjutningar)
- O-ord (underprogram, loopar, villkor)
- `G18`/`G19`, splines, cykler (`G81` m.fl.) och axlarna A, B, C, U, V, W
- koordinater som beror på värden bara LinuxCNC känner till, t.ex. `X#<_hal[…]>`

Enkla uttryck och egna parametrar, t.ex. `X[#<w>/2]`, räknas ut och går bra.

## Tester

```bash
python -m pip install pytest
```

```bash
python -m pytest tests
```

På Windows hoppas ett test över i fönsterlöst läge. Det körs med `QT_QPA_PLATFORM=windows` satt,
och då öppnas riktiga fönster en kort stund.
