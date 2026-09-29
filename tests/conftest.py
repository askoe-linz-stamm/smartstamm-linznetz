"""Shared fixtures and helpers."""

from datetime import datetime, timedelta

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """Load custom_components/ in every test; the integration depends on the recorder."""


def portal_csv(first: datetime, kwh: list[float]) -> str:
    """CSV like the portal export: consecutive local quarter hours from ``first``."""
    lines = ["Datum von;Datum bis;Energiemenge in kWh;Ersatzwert"]
    for i, value in enumerate(kwh):
        start = first + timedelta(minutes=15 * i)
        end = start + timedelta(minutes=15)
        amount = f"{value:.3f}".replace(".", ",")
        lines.append(f"{start:%d.%m.%Y %H:%M};{end:%d.%m.%Y %H:%M};{amount};")
    return "\r\n".join(lines) + "\r\n"
