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
| Rektangulär plåt | Bredd och höjd i panelen. Plåtens nedre vänstra hörn är G-kodens X0 Y0. |
| Rita plåten | **Rita**, och klicka ut hörnen med musen. Avsluta med dubbelklick, Enter eller klick på första hörnet. Backsteg tar bort senaste hörnet, Esc avbryter och Shift ger raka kanter. Hörnen hamnar på hela millimeter. |
| Skriva plåtens hörn | **Hörn…** visar hörnen i en tabell, där du kan ändra, lägga till och ta bort dem. Använd den för exakta mått. |
| Plåt från maskinen | Jogga till plåtens hörn och tryck på användarknappen **Spara punkt** i QtPlasmaC vid varje hörn. Öppna **Från maskinen…** i PlasmaNest, där punkterna dyker upp efter hand i en tabell och en ritning. I ritningen syns kanterna, den senaste punkten och plåten efter marginalen. Välj säkerhetsmarginal och tryck **Använd som plåt**. Se nedan hur knappen läggs in. |
| Tillbaka till rektangel | **Rektangel** ger en rektangel med polygonens yttermått, med hörnet i X0 Y0. |
| Flytta | Dra med vänster musknapp, skriv X/Y i panelen, eller använd piltangenterna (1 mm, med Shift 10 mm). |
| Rotera | Håll höger musknapp på figuren och dra runt den, så följer figuren muspekaren (Ctrl ger steg om 15°). Det går också med den gröna ringen, Shift+mushjul, tangenten R, knapparna i panelen, eller genom att skriva vinkeln. |
| Markera flera | Dra en ruta på tom yta, eller Ctrl-klicka. Flera figurer roterar som en grupp. |
| Rita rektangel | **Rita rektangel**: en etikett vid muspekaren visar X och Y. Tryck i ett hörn och dra till motsatta hörnet, eller klicka i båda. Etiketten visar bredd × höjd. Hörnen hamnar på hela millimeter, och Esc avbryter. Därefter kan exakt **Bredd** och **Höjd** skrivas i panelen: skriv bredden, tryck Tab, skriv höjden och tryck Enter. Det första hörnet står still. |
| Rita cirkel | **Rita cirkel**: tryck i centrum och dra ut till kanten, eller klicka i centrum och vid kanten. Etiketten visar X och Y och sedan diametern i hela millimeter. Exakt **Diameter** skrivs i panelen. |
| Kopiera | **Kopiera…** eller Ctrl+D, och ange antal. Kopiorna läggs på lediga platser. |
| Ta bort | **Ta bort** (markerade figurer) eller **Ta bort hela G-koden** (alla kopior av filen). |
| Skärordning | Listan visar ordningen. Ändra med **Skär tidigare** och **Skär senare**. |
| Ångra | Ctrl+Z. Gör om med Ctrl+Y (Linux: Ctrl+Shift+Z). |
| Vy | Mushjul zoomar, mittenknappen eller högerdrag på tom yta panorerar, F anpassar till fönstret. |

Figurer som ligger utanför plåten blir **röda** och figurer som överlappar blir **orange**. Att
lägga en figur inuti ett hål i en annan räknas inte som överlapp. Kontrollen gäller den
programmerade banan, så skärspalten (kerf) räknas inte med: lämna själv tillräckligt avstånd.

Plåten kan ha vilken form som helst så länge kanterna inte korsar varandra, till exempel en
restbit med urtag. Den behöver inte ligga vid G-kodens X0 Y0, som visas med ett lila kors, men
hörnens koordinater måste stämma med hur plåten ligger i maskinen. Plåtens form är bara en
kontroll: den påverkar inte G-koden, och QtPlasmaC känner inte till den.

## Spara plåtens hörn från maskinen

Lägg in knappen en gång i QtPlasmaC, under **SETTINGS** → **USER BUTTONS** på en ledig rad:

| Name | Code |
|---|---|
| `Spara\punkt` | `%/home/debian/src/plasmanest/save_point.py` |

Tryck **Save**, så dyker knappen upp på MAIN-fliken. Ändra sökvägen om PlasmaNest ligger någon annanstans.

- Maskinen måste vara referenskörd. Varje tryck sparar en punkt, som syns i PlasmaNest. QtPlasmaC
  visar bara ett meddelande om punkten inte kunde sparas.
- Är lasern på (knappen **Laser**) sparas punkten under laserkorset, annars punkten under brännaren.
  Laserns förskjutning läses från `[LASER_OFFSET]` i QtPlasmaC:s prefs-fil.
- Punkterna sparas i `~/.plasmanest/points.txt` i **maskinkoordinater**. PlasmaNest räknar om dem
  med det origo LinuxCNC har när plåten skapas, så det går bra att nollställa origo efter mätningen.
  Nollställer du origo efter att plåten skapats måste du skapa den igen.
- Ta hörnen i ordning runt plåten. **Rensa alla** innan en ny plåt mäts upp.
- Säkerhetsmarginalen flyttar varje kant inåt. Blir formen ogiltig av marginalen vägras plåten.

### Spara punkt från handkontrollen

En knapp på handkontrollen kan spara punkten på samma sätt. Lägg till en rad sist i
postgui-HAL-filen (här `custom_postgui.hal`) och starta om LinuxCNC:

```
loadusr python3 /home/debian/src/plasmanest/point_button.py input.0.btn-start
```

Sista ordet är knappens HAL-pinne. `input.0.btn-start` är START på en Logitech F310 via
`hal_input`. Skriptet läser bara pinnen och kopplar inte om något. Det laddas utan `-W`, så LinuxCNC
startar även om skriptet saknas. Fel visas i QtPlasmaC.

## Öppna exporten i QtPlasmaC med en knapp

På en Linux-dator med `~/linuxcnc/nc_files` föreslår **Exportera** katalogen
`~/linuxcnc/nc_files/plasmanest/`. Lägg in en knapp i QtPlasmaC under **SETTINGS** → **USER BUTTONS**:

| Name | Code |
|---|---|
| `Ladda\nest` | `latest-file /home/debian/linuxcnc/nc_files/plasmanest` |

Knappen öppnar den senast exporterade `.ngc`-filen i katalogen. Den fungerar bara när maskinen
står still. Exporterar du till en annan katalog hittar knappen inte filen, och statusraden i
PlasmaNest påminner om det.

## PlasmaNest som flik i QtPlasmaC

PlasmaNest kan visas som fliken **PLASMANEST** i QtPlasmaC (2.9). Det är hittills bara provat i
QtPlasmaC:s simulator. Lägg till under `[DISPLAY]` i INI-filen och starta om LinuxCNC:

```
USER_COMMAND_FILE = /home/debian/src/plasmanest/qtplasmac/user_command.py
```

- PlasmaNest startar när fliken väljs första gången och avslutas med QtPlasmaC. Det körs som ett
  eget program, så ett fel i PlasmaNest påverkar inte QtPlasmaC.
- När du går från PLASMANEST till MAIN läses nesten in i förhandsvisningen. Det sker bara om den
  har ändrats och maskinen står still. Figurer utanför plåten, överlapp och materialvarningar ger
  ett felmeddelande i stället. Kryssrutan **Läs in i QtPlasmaC vid byte till MAIN** stänger av det.
- Fliken är spärrad medan maskinen kör, som QtPlasmaC:s andra flikar.
- Osparat arbete sparas i `~/.plasmanest/autosave.pnest` när QtPlasmaC stängs.

## Rektanglar och cirklar som ritas i PlasmaNest

När en rektangel eller cirkel är markerad visar panelen rutan **Rektangel** eller **Cirkel**:

| Fält | Betydelse |
|---|---|
| Bredd, Höjd, Diameter | De ritade måtten. |
| Material | Ur QtPlasmaC:s materialfil. Väljs med `M190` i G-koden, och skärspalten hämtas från materialet. "Aktivt i QtPlasmaC" väljer inget material. |
| Skär | **Utsida (delen):** banan går en halv skärspalt utanför de ritade måtten, medurs. **Insida (hål):** en halv skärspalt innanför, moturs. |
| Skärspalt | Hämtas från materialet. Skrivs för hand när inget material är valt. |
| Inledning | Längd i mm, 0 eller mer. En rak linje vinkelrätt mot kanten (radiell för cirklar), med början i spillet. |
| Placera inledning | Peka på eller nära figuren och klicka där inledningen ska vara. |

- Fälten **X** och **Y** gäller rektangelns ritade hörn och cirkelns centrum, inte banans yttermått.
- Delar skärs medurs och hål moturs, cirklar med G2 och G3.
- Små hål får ingen sänkt hastighet eller överbränning.
- En ny rektangel får samma inställningar som den senast ändrade.
- Inställningarna sparas i rektangelns G-kod och finns kvar i projektfiler.
- En omöjlig inställning vägras med ett rött meddelande, till exempel en inledning som inte får
  plats i hålet.

## Utseende

PlasmaNest har QtPlasmaC:s färger, som läses från QtPlasmaC:s inställningar (`[COLOR_OPTIONS]`).
Ändras färgerna i QtPlasmaC följer PlasmaNest med vid nästa start.

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
