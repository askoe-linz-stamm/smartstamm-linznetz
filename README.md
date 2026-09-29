# Linz Netz for Home Assistant

Imports the smart meter consumption of a Linz Netz customer into Home Assistant's long-term statistics, so it shows up in the Energy dashboard. Built for SmartStamm, the Home Assistant Green of ASKÖ Linz-Stamm.

Linz Netz offers no API. The integration signs in to the [Serviceportal](https://services.linznetz.at/verbrauchsdateninformation/consumption.jsf) like a browser and downloads the CSV export of the quarter-hour values. A portal redesign can break it.

## What you get

- `linznetz:energy_consumption`: hourly consumption in kWh (quarter hours summed to hours).
- `linznetz:energy_cost`: hourly energy cost in EUR, if a price entity is set. Cost is kWh × the entity's current value in EUR/kWh at import time. No base fee, grid fees or taxes.
- Sensor **Daten bis**: end of the newest imported hour. Linz Netz publishes values roughly once a day, so this usually lags by about a day.

## Setup

1. Activate quarter-hour values (*Viertelstundenwerte aktivieren*) in the Serviceportal.
2. Install through HACS: add `https://github.com/askoe-linz-stamm/smartstamm-linznetz` as a custom repository (type *Integration*), download **Linz Netz**, restart Home Assistant.
3. Add the integration **Linz Netz** with the portal login. Optionally choose an energy price entity (for example an `input_number` in EUR/kWh); it can be changed later under *Configure*.
4. In *Settings → Dashboards → Energy*, add a grid consumption source with the statistic **Linz Netz Stromverbrauch** and, for costs, **Linz Netz Energiekosten**.

## How it works

Every six hours (and on startup) the integration makes four small requests: open the portal page (signing in when the session expired), switch to quarter-hour values, show the period, download the CSV. The first run requests three years; the portal returns whatever exists. Later runs request the last seven days before the newest imported hour, so late or corrected values replace earlier ones. Only complete hours are imported.

On a Home Assistant Green a run takes about one second. There are no Python dependencies beyond Home Assistant itself.

If the portal rejects the password, Home Assistant asks for a new one (re-authentication).

## Development

```
python3.14 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest
```
