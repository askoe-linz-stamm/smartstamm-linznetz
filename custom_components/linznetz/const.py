"""Constants for the Linz Netz integration."""

from datetime import timedelta

DOMAIN = "linznetz"

CONF_PRICE_ENTITY = "price_entity"
# Entity whose state is the total of all fixed costs in EUR per year.
CONF_FIXED_COST_ENTITY = "fixed_cost_entity"

CONSUMPTION_STATISTIC = f"{DOMAIN}:energy_consumption"
COST_STATISTIC = f"{DOMAIN}:energy_cost"

# The portal publishes values about once a day; each fetch is a few small requests.
UPDATE_INTERVAL = timedelta(hours=6)
# Used while the previous Vienna day is not imported yet, so it arrives soon after
# Linz Netz publishes it (usually in the morning).
MISSING_DAY_INTERVAL = timedelta(hours=1)
# A data horizon older than this after a successful fetch shows the notice ``stale``.
# Normally it lags by up to a day and a half, until the morning publication.
STALE_AFTER = timedelta(days=2)
# Days reloaded on every run so late or corrected values replace earlier ones.
REFETCH_DAYS = 7
# History requested on the first run; the portal returns whatever exists.
HISTORY_DAYS = 3 * 365
