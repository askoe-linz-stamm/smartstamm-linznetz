"""Constants for the Linz Netz integration."""

from datetime import timedelta

DOMAIN = "linznetz"

CONF_PRICE_ENTITY = "price_entity"

CONSUMPTION_STATISTIC = f"{DOMAIN}:energy_consumption"
COST_STATISTIC = f"{DOMAIN}:energy_cost"

# The portal publishes values about once a day; each fetch is a few small requests.
UPDATE_INTERVAL = timedelta(hours=6)
# Days reloaded on every run so late or corrected values replace earlier ones.
REFETCH_DAYS = 7
# History requested on the first run; the portal returns whatever exists.
HISTORY_DAYS = 3 * 365
