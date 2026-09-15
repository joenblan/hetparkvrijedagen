# Vrije dagen — automatische pagina

Genereert elke nacht `docs/index.html` en `docs/vrije-dagen.json` met alle vrije
dagen van het lopende schooljaar (1 september t.e.m. 30 juni), en publiceert die
via GitHub Pages.

## Bronnen

| Wat | Waar vandaan |
| --- | --- |
| Herfst-, kerst-, krokus-, paas- en zomervakantie | vlaanderen.be |
| Wapenstilstand, paasmaandag, 1 mei, Hemelvaart + dag erna, pinkstermaandag | vlaanderen.be |
| Pedagogische studiedagen, facultatieve verlofdagen, brugdagen | Google Calendar van de school |

De schoolkalender wordt **niet** gebruikt voor vakanties en feestdagen, ook niet
als die er toevallen in staan. Zo kan er niets dubbel op de pagina komen.

## Eenmalig instellen

1. **Kalender publiek zetten.** Google Calendar → instellingen van
   *Gemeenteschool Melle (uitstappen)* → *Toegangsmachtigingen* →
   *Openbaar beschikbaar maken*. Controleer daarna:

   ```
   curl -sI "https://calendar.google.com/calendar/ical/mellegbs%40gmail.com/public/basic.ics" | head -1
   ```

   Krijg je een 404, dan staat de kalender niet publiek. Gebruik dan het
   *geheime adres in iCal-indeling* onderaan diezelfde instellingenpagina en zet
   dat als repository secret `CALENDAR_ICS_URL`.

2. **Workflow-rechten.** Settings → Actions → General → Workflow permissions →
   *Read and write permissions*. Anders mag de bot niet terugpushen.

3. Zet de bestanden in de repo en push. De eerste run kan je met de knop
   *Run workflow* handmatig starten.

## Lokaal draaien

```bash
pip install -r requirements.txt
python scripts/build_vrije_dagen.py
python scripts/build_vrije_dagen.py --vandaag 2027-03-01   # ander moment testen
python scripts/build_vrije_dagen.py --ics-file tests/schoolkalender_fixture.ics
```

## Knoppen in `scripts/build_vrije_dagen.py`

- `INCLUDE_ZOMERVAKANTIE` — de zomervakantie begint op 1 juli en valt dus net
  buiten het venster. Staat standaard aan omdat ouders die datum verwachten.
- `ROLLOVER_MONTH` — vanaf 1 juli toont de pagina het schooljaar dat in
  september begint.
- `CLASSIFICATIE` — de regexes die bepalen welke kalenderitems meetellen.
  Alles wat niet matcht (uitstappen, zwemmen, oudercontacten) wordt genegeerd.

## GitHub Pages

Settings → Pages → Source: *Deploy from a branch* → branch `main`, map `/docs`.
De pagina staat daarna op `https://<gebruiker>.github.io/<repo>/`.

GitHub Pages serveert alleen vanuit de repo-root of vanuit `/docs`; `/public`
werkt niet. Daarom schrijft het script naar `docs/`.

Wil je de lijst later in een andere site tonen: `docs/vrije-dagen.json` is
dezelfde data in JSON, en in `docs/index.html` staat het herbruikbare stuk tussen
de commentaren `vanaf hier is het fragment` en `einde fragment`.

## Als vlaanderen.be verandert

De parser leest de tekst van de pagina, niet de HTML-structuur, dus een
herontwerp breekt hem meestal niet. Verandert de bewoording wél, dan:

1. mislukt de controle (`ontbreekt: herfstvakantie`, …),
2. valt de build terug op `data/schoolvakanties-cache.json`,
3. blijft de pagina dus correct staan tot je het fixt.

Die cache wordt na elke geslaagde run bijgewerkt en meegecommit.
