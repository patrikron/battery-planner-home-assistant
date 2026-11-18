# Batteriplanerare – kontext för nya sessioner

Läs hela filen innan du ändrar något. Repot är publikt: skriv bara teknisk information om koden här, aldrig personliga uppgifter, entitets-id från en verklig installation eller länkar till privata dokument.

## Komponenten

En egen custom integration, `battery_planner` ("Batteriplanerare"). Version i koden: **0.6.6**.

- **Skuggläge:** planerar och simulerar ett virtuellt batteri, men styr ingenting. Skuggläget får aldrig ändra något i Home Assistant.
- Styrning ska vara ett eget läge, avstängt som standard.

### Filer (`custom_components/battery_planner/`)

| Fil | Innehåll |
|---|---|
| `optimizer.py` | Dynamisk programmering över diskretiserad laddnivå (steg 0,25 kWh), i ren Python utan scipy.<br>`BatteryParams` innehåller `wear_cost`, `max_soc`, `high_soc`, `hold_cost`, `sell_margin` (straff per såld kWh, bara i planeringen) `export_limit_kwh` (säkringen begränsar export) och `sell_buffer`. Med buffert körs optimeringen först med lasten × (1 + buffert), och den planens försäljning per timme blir ett tak (plus ett laddsteg) för den riktiga planen.<br>`optimize()` tar fram planen. Slutvärdet är undre kvartilens pris × verkningsgrad × 0,9.<br>`apply_hour()` simulerar en verklig timme. |
| `forecast.py` | Profil per veckodag och timme (4 veckor) med inlärd korrigering per timme.<br>Uppvärmningsmodell och eldfaktorer: kamin över tröskeln (standard 40 °C) räknas som eld.<br>Eldfaktorer för 0–8 h efter eld lärs in ur historiken. |
| `loads.py` | `vvb_schedule()` läser varmvattenplanerarens `periods` (`s`/`e` i ms, `kwh`).<br>`car_need_kwh()`.<br>`schedule_car()` väljer de billigaste timmarna före klar-tiden inom säkringen.<br>`spans()`. |
| `coordinator.py` | Uppdateras var 15:e minut och vid minut 1 varje timme. Använder Store för tillståndet.<br>`_history()` läser timstatistik via recorder.<br>`_solar_forecast()` hämtar solprognos via Energipanelens plattformar.<br>`_pv_ratio()` justerar solprognosen med en faktor 0,4–1,6.<br>`_book_finished_hours()` bokför scenarier, cykler och prognos mot utfall.<br>`_health()` och `_summary()`. |
| `sensor.py` | `PlanSensor` har attributen `plan`, `uppvarmning`, `elbil`, `varmvatten`, `sol`, `scenarier`, `dagar` och `halsa`.<br>Övriga sensorer: laddnivå, kWh, besparing (idag, månad, totalt), prognosfel, elbil, solprognos och laddcykler. |
| `binary_sensor.py` | "Elbil ska ladda nu" och "Batteri laddar från nätet nu". |
| `config_flow.py` | Ett enda formulär med alla fält, plus ett options flow med samma formulär. |
| `www/battery-planner-card.js` | Två kort, som serveras automatiskt via `add_extra_js_url`:<br>`custom:battery-planner-card` visar plan, laddnivå och besparing.<br>`custom:battery-planner-insights-card` visar scenarier, besparing per dag och prognos mot utfall. |

### Viktiga beslut och lärdomar

- **Laster som planeras själv:** varmvatten och bil tas bort ur historiken och planeras separat. I backtest sjönk prognosfelet då från 64 % till cirka 25 %.
- **Inomhustemperatur används inte:** den testades men försämrade prognosen, eftersom den är en effekt av uppvärmningen och inte en orsak.
- **Uppvärmning:** prognosen bygger på en ren timprofil × temperaturkvot (bara när det är mer än 3 °C från snittet) × eldfaktorer. En linjär temperaturregression gav sämre resultat.
- **Med solprognos:** lasten blir husets totala förbrukning, och solen dras av timme för timme. Utan solprognos används förra veckans export som solöverskott.
- **Priser** kan komma per kvart (96 per dygn). `hourly_prices()` gör om dem till timmedel.
- **Rekommenderad slitagekostnad:** 0,15–0,20 kr/kWh. Standardvärdet 0,05 är för lågt. Garantin på Growatt APX är 15,3 MWh per modul, 10 år och 60 % restkapacitet.
- **Säkring:** 16 A × 3 faser ger 11,04 kWh per timme totalt.

### Growatt-reglage i Home Assistant (för styrning)

Den lokala Growatt Modbus-integrationen för MID-XH ger entiteter med prefixet `growatt_tl_x_xh_tl3_xh_`:

- `select.*_work_mode_slot_{1..4}_priority`: Load first, Battery first eller Grid first.
- `select.*_work_mode_slot_{1..4}_start` och `_end` (15-minutersteg).
- `switch.*_work_mode_slot_{1..4}_enabled`.
- `select.*_battery_first_grid_charge`: Disabled eller Enabled.
- `number.*_battery_first_charge_rate` (%) och `number.*_battery_first_stop_charge` (%).
- `number.*_grid_first_discharge_rate` (%), `number.*_grid_first_stop_discharge` och `number.*_grid_first_stop_discharge_on_grid`.
- Sensorerna `sensor.*_battery_voltage`, `_current` och `_power` är "unknown" tills ett batteri sitter i.

MID-XH laddar och levererar med största sannolikhet lika mycket på alla tre faser. Ingen styrning är testad mot riktig hårdvara.

## Att göra

1. **Styrläge (`MODE_CONTROL`)**, avstängt som standard:
   - Översätt planen till lägen: ladda från nätet = Battery first + nätladdning på, normal drift = Load first, export = Grid first.
   - Skriv sällan till växelriktaren, och falla tillbaka till Load first om planen saknas.
   - Läs riktig laddnivå från batteriet i stället för den virtuella.
   - Testa stegvis när batteriet är installerat: först manuellt, sedan en natt med automatik.
2. **Säkringsskydd per fas:** sänk `battery_first_charge_rate` så att den tyngsta fasen håller sig under säkringen. Växelriktaren fördelar effekten lika på alla faser.
3. **Batteritemperatur och kvartsupplösning** (bess-manager planerar per 15 minuter).
4. **Små nätladdningar mitt på dagen:** planen lägger ibland in 0,05–0,2 kWh nätladdning kl. 11–14. Det beror på avrundning till 0,25 kWh-steg och bör städas bort.
5. **Solprognosen via Energipanelen** är bara testad med en simulerad prognos. Om kortet visar "export senaste veckan" som källa fungerar kopplingen inte.

Referens: [johanzander/bess-manager](https://github.com/johanzander/bess-manager) är ett tillägg (add-on) med färdig Growatt-styrning (MID via Modbus eller moln), säkringsskydd och solprognos.

## Utveckling och test

```bash
pip install pytest-homeassistant-custom-component   # testat med 0.13.316 / HA 2026.2.3
python3 -m pytest -q tests    # körs från repots rot, 14 tester ska gå igenom
```

- `tests/conftest.py` begär `recorder_mock` före `enable_custom_integrations`, och `pytest.ini` sätter `asyncio_mode = auto`.
- `http` och `frontend` är `after_dependencies`, och kortet registreras bara om de finns. Annars går testerna sönder.
- Koden ligger i GitHub-repot `patrikron/battery-planner-home-assistant` (gren `main`). Höj versionen på tre ställen vid varje release: `const.py`, `manifest.json` och `CARD_VERSION` i kortet.
- `.github/workflows/release.yml` skapar en GitHub-release `vX.Y.Z` när versionen i `manifest.json` ändras på `main` (och kontrollerar att de tre ställena stämmer). HACS visar annars bara commit-hashen. Releasen kan också startas för hand under Actions → Release → Run workflow.
- Installation för hand: kopiera `custom_components/battery_planner` till `/config/custom_components/`, starta om Home Assistant, fyll i nya fält under Batteriplanerare → Konfigurera och ladda om webbläsaren med Ctrl+F5.
