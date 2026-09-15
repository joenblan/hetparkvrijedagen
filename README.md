# Vrije dagen — automatische pagina

Genereert elke nacht `public/vrije-dagen.html` en `public/vrije-dagen.json` met
alle vrije dagen van het lopende schooljaar (1 september t.e.m. 30 juni).

## Bronnen

| Wat | Waar vandaan |
| --- | --- |
| Herfst-, kerst-, krokus-, paas- en zomervakantie | vlaanderen.be |
| Wapenstilstand, paasmaandag, 1 mei, Hemelvaart + dag erna, pinkstermaandag | vlaanderen.be |
| Pedagogische studiedagen, facultatieve verlofdagen, brugdagen | Google Calendar van de school |

De schoolkalender wordt **niet** gebruikt voor vakanties en feestdagen, ook niet
als die er toevallen in staan. Zo kan er niets dubbel op de pagina komen.

## Eenmalig instellen

1. **Kalender publiek zetten.** Google Calendar → instellingen van Kalender → *Toegangsmachtigingen* →
   *Openbaar beschikbaar maken*. Controleer daarna:

2. **Workflow-rechten.** Settings → Actions → General → Workflow permissions →
   *Read and write permissions*. Anders mag de bot niet terugpushen.

3. Zet de bestanden in de repo en push. De eerste run kan je met de knop
   *Run workflow* handmatig starten.

## Knoppen in `scripts/build_vrije_dagen.py`

- `INCLUDE_ZOMERVAKANTIE` — de zomervakantie begint op 1 juli en valt dus net
  buiten het venster. Staat standaard aan omdat ouders die datum verwachten.
- `ROLLOVER_MONTH` — vanaf 1 juli toont de pagina het schooljaar dat in
  september begint.
- `CLASSIFICATIE` — de regexes die bepalen welke kalenderitems meetellen.
  Alles wat niet matcht (uitstappen, zwemmen, oudercontacten) wordt genegeerd.

## Hoe de pagina in je site komt

`public/vrije-dagen.html` is een volledige pagina. Wil je alleen het stuk binnen
je eigen layout, neem dan het blok tussen de HTML-commentaren
`vanaf hier is het fragment` en `einde fragment`, plus de `<style>` uit de head.

Werk je met een SSG (Jekyll, Eleventy, Hugo …), gebruik dan liever
`public/vrije-dagen.json` als databron en laat je eigen template het renderen.

## Als vlaanderen.be verandert

De parser leest de tekst van de pagina, niet de HTML-structuur, dus een
herontwerp breekt hem meestal niet. Verandert de bewoording wél, dan:

1. mislukt de controle (`ontbreekt: herfstvakantie`, …),
2. valt de build terug op `data/schoolvakanties-cache.json`,
3. blijft de pagina dus correct staan tot je het fixt.

Die cache wordt na elke geslaagde run bijgewerkt en meegecommit.
