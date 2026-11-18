# Batteriplanerare för Home Assistant

Planerar när ett hembatteri ska laddas och användas, timme för timme, utifrån spotpris och en förbrukningsprognos som byggs från din egen historik.

**Version 0.6 – skuggläge med två Lovelace-kort, batterihälsa, solprognos, scenariojämförelse, eldningsmedveten prognos och planering av varmvatten och elbil.** Planeraren styr ingenting. Den räknar fram en plan varje kvart och simulerar sedan, timme för timme, vad ett batteri hade gjort med din *verkliga* förbrukning och dina *verkliga* priser. Så kan du se vad batteriet skulle ha sparat, och hur bra prognosen är, innan batteriet är installerat.

## Så fungerar den

1. **Prognos:** snittförbrukning per timme för samma veckodag de senaste fyra veckorna (köpt el) och senaste veckan (såld el/solöverskott). En korrigeringsfaktor per timme lär sig av hur väl prognosen stämde.
2. **Optimering:** dynamisk programmering över batteriets laddnivå för alla timmar med kända priser (upp till 36 h). Hänsyn tas till kapacitet, reserv, laddeffekt, verkningsgrad, slitage och huvudsäkringen.
3. **Skuggsimulering:** varje ny timme bokförs den föregående: planen följs så långt verkligheten tillåter, och skillnaden mot att inte ha batteri sparas som besparing.

Testat mot verklig data för 1 okt – 7 nov 2026 (23,04 kWh, 5 kW, 16 A): planeraren med prognos nådde 452 kr, mot 541 kr för en teoretiskt perfekt plan (84 %).

## Installation

1. Kopiera mappen `custom_components/battery_planner` till `/config/custom_components/` i Home Assistant.
2. Starta om Home Assistant.
3. Inställningar → Enheter och tjänster → Lägg till integration → **Batteriplanerare**.
4. Välj:
   - **Prissensor:** köppris i kr/kWh, till exempel en Nord Pool-sensor med påslag och skatter
   - **Försäljningspris:** försäljningspris i kr/kWh, till exempel rent spotpris
   - **Köpt från nätet:** energisensor (kWh) för köpt el, till exempel från växelriktaren eller elmätaren
   - **Såld till nätet:** energisensor (kWh) för såld el
   - Batteri: kapacitet, reserv, laddeffekt, verkningsgrad och huvudsäkring

Alla värden kan ändras efteråt under Konfigurera.

## Entiteter

| Entitet | Innehåll |
| --- | --- |
| `sensor.batteriplanerare_plan` | Vad batteriet gör nu (Laddar/Levererar/Väntar). Attributet `plan` har hela planen per timme. |
| `sensor.batteriplanerare_virtuell_laddniva` | Simulerad laddnivå i % |
| `sensor.batteriplanerare_virtuell_energi_i_batteriet` | Simulerad energi i kWh |
| `sensor.batteriplanerare_besparing_idag` | Simulerad besparing idag |
| `sensor.batteriplanerare_besparing_denna_manad` | Simulerad besparing denna månad |
| `sensor.batteriplanerare_besparing_totalt` | Total simulerad besparing, attribut `per_dag` |
| `sensor.batteriplanerare_prognosfel_senaste_veckan` | Prognosens avvikelse i % av faktisk förbrukning |
| `sensor.batteriplanerare_elbil_planerad_laddning` | kWh som planeras in i bilen före klar-tiden, attribut med perioder och behov |
| `binary_sensor.batteriplanerare_elbil_ska_ladda_nu` | `on` när planen säger att bilen ska ladda den här timmen |
| `binary_sensor.batteriplanerare_batteri_laddar_fran_natet_nu` | `on` när planen laddar batteriet från nätet den här timmen |
| `sensor.batteriplanerare_batterioverskott` | kWh som batteriet har över reserven innan det laddas nästa gång, med buffert för prognosfel |
| `binary_sensor.batteriplanerare_element_far_kora_pa_batteriet` | `on` när överskottet räcker till den inställda effekten i minst en timme |

## Element på batteriöverskott (valfritt)

Prisstyrda element brukar stängas av när elen är dyr. Med batteri finns det ofta energi kvar när kvällens dyra timmar är slut, och då kan elementen lika gärna få köra på den.

- `sensor.batteriplanerare_batterioverskott` tittar på planen fram till nästa gång batteriet laddas (från nätet eller solen). Fram till dess sjunker laddnivån hela tiden, så det som finns kvar över reserven då är överskott. Planerad urladdning räknas med en buffert (standard 20 %) för att förbrukningen kan bli högre än prognosen.
- Ange elementens effekt i kW under Konfigurera ("Effekt som får köras på batteriöverskott"). Då slås `binary_sensor.batteriplanerare_element_far_kora_pa_batteriet` på när överskottet räcker en hel timme med den effekten, och av när det räcker mindre än en halvtimme. 0 kW stänger av funktionen.
- Överskottet räknas om var 15:e minut. Drar elementen ström sjunker laddnivån, och binärsensorn slås av innan batteriet behövs till de dyra timmarna.
- Medan batteriet laddar är överskottet 0.

Behåll din vanliga prisstyrning och lägg till villkoret: element på om priset är lågt **eller** binärsensorn är på.

## Uppvärmning och kamin (valfritt)

Anger du energisensorer för uppvärmning (luftvärmepump, element), utetemperatur, väderprognos och en temperatursensor vid kaminen delar planeraren upp prognosen i grundförbrukning och uppvärmning:

- Uppvärmningen per timme räknas fram från timmar utan eld, och skalas upp eller ner när väderprognosen avviker mer än 3 °C från det vanliga.
- När kaminsensorn är över tröskeln (standard 40 °C) räknas det som att det eldas. Planeraren lär sig ur historiken hur mycket uppvärmningen minskar medan det brinner och timmarna efteråt, och hur länge en eldning brukar hålla.
- Attributet `uppvarmning` på plansensorn visar om det eldas, eldfaktorerna och uppvärmningsprognosen för kommande dygn. Kortet visar en rad när det eldas.

Exempel: energisensorer för värmepump och element, en utetemperatursensor, en `weather`-entitet med timprognos och en temperatursensor monterad vid kaminen.

## Solprognos och scenarier (valfritt)

Anger du en energisensor för solproduktionen använder planeraren solprognosen som redan är kopplad till Energipanelen (till exempel Forecast.Solar eller Solcast):

- Prognosen räknas då på husets hela förbrukning, och solen dras av timme för timme. Utan solsensor används förra veckans export som tidigare.
- Planeraren jämför solprognosen mot verklig produktion de senaste dygnen och justerar den med en faktor mellan 0,4 och 1,6.
- Varje bokförd timme räknas i tre scenarier: bara nät, med sol, och med sol och batteri. Attributet `scenarier` på plansensorn har summorna för idag, månaden och totalt.
- Attributet `dagar` har per dag kostnaderna, prognosen som gjordes vid midnatt och utfallet.
- `sensor.batteriplanerare_solprognos_kommande_dygn` visar solprognosen för de närmaste 24 timmarna.

Exempel: växelriktarens sensor för total solproduktion (kWh).

## Batteriets hälsa

Planeraren väger varje laddcykel mot vad den sliter på batteriet:

- **Slitagekostnad (kr/kWh).** Varje kWh som tas ur batteriet kostar så mycket i kalkylen. Planeraren laddar bara från nätet när prisskillnaden täcker både förlusterna och slitaget. Den bokförda besparingen är redan minskad med slitaget.
- **Marginal innan batteriet säljer (kr/kWh).** Gäller bara när försäljning från batteriet är tillåten. En försäljning måste ge så här mycket mer per kWh än alternativet (spara energin eller låta bli att ladda) innan den planeras. Standard är 0,10. Marginalen räknas bara in i planeringen, inte i den bokförda besparingen. Vid pristoppar, till exempel 10 kr/kWh en timme, är marginalen liten i jämförelse: planeraren laddar i förväg och säljer med full urladdningseffekt under toppen, men aldrig mer än huvudsäkringen klarar och aldrig under reserven.
- **Buffert för förbrukningen innan batteriet säljer (%).** Standard 20 %. Planeraren gör först en plan som om huset drog så mycket mer än prognosen, och säljer aldrig mer än den försiktiga planen skulle göra. Så räcker batteriet till de dyra timmarna även om förbrukningsprognosen slår fel. 0 % stänger av bufferten.
- **Högsta laddnivå.** Sätt till exempel 90 % om du vill att batteriet aldrig laddas fullt.
- **Sen laddning.** När två planer kostar lika mycket väljs den där batteriet står kortast tid över 80 %.
- **Laddcykler och garanti.** `sensor.batteriplanerare_laddcykler` räknar ekvivalenta fulla cykler. Anger du garanterad genomströmning (till exempel 61,2 MWh för fyra Growatt APX-moduler) visas hur stor del av garantin som är förbrukad och hur många år den räcker i nuvarande takt.

## Varmvatten och elbil (valfritt)

I stället för att gissa när varmvattenberedaren och bilen går planerar planeraren in dem:

- **Varmvatten:** läser perioderna från din befintliga varmvattenplanerare (attributet `periods` med `s`, `e`, `kwh`). Varmvattenplaneraren bestämmer fortfarande, batteriplaneraren bara räknar med lasten.
- **Elbil:** räknar behovet från laddnivå, målnivå och batteristorlek, och lägger laddningen i de billigaste timmarna före klar-tiden (standard 07:00) utan att gå över huvudsäkringen tillsammans med resten av huset. Är bilen inte inkopplad planeras ingen laddning.
- Historiken för de två rensas bort ur grundprognosen, så de inte räknas dubbelt.

I backtest mot verklig data sjönk prognosfelet från 64 % till cirka 25 % när varmvatten och bil planerades i stället för att ingå i den vanliga förbrukningsprofilen.

Exempel: varmvattenplanerarens statussensor, en energisensor på beredaren, bilens laddnivå och målnivå, en binärsensor för om bilen är inkopplad och laddboxens energisensor.

## Kända begränsningar

- Utan solsensor prognostiseras solöverskottet från senaste veckans export.
- Bilplanen styr inte go-e-laddaren. Vill du följa den kan du låta en egen automation använda `binary_sensor.batteriplanerare_elbil_ska_ladda_nu`.
- Säkringen räknas som totalt 16 A × 3 faser (fasbalansering antas, som i Solinteg).
- Ingen styrning av växelriktaren – den kommer i en senare version (SolaX Modbus, EMS BattCtrl).

## Lovelace-kort

Kortet följer med komponenten och laddas automatiskt – ingen resurs behöver läggas till. Lägg till det på en instrumentpanel:

```yaml
type: custom:battery-planner-card
```

Valfria inställningar: `title`, `hours` (antal timmar i diagrammet, standard 24) och egna entiteter (`entity`, `soc_entity`, `soc_kwh_entity`, `today_entity`, `month_entity`, `error_entity`).

Kortet visar laddnivå, vad batteriet gör nu, besparing idag och denna månad, ett diagram med elpris, planerad laddning (cyan), urladdning (amber) och laddnivå (vit linje), samt nästa planerade händelser.

### Insiktskortet

```yaml
type: custom:battery-planner-insights-card
```

Visar scenariojämförelsen (bara nät, med sol, sol + batteri) för idag, månaden eller totalt, besparing per dag och prognos mot utfall för förbrukning och sol. Kortet fylls på när första dygnet är bokfört.
