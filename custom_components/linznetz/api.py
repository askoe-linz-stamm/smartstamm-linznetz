"""Client for the Linz Netz Serviceportal (no official API, plain HTTP).

The portal is a Jakarta Faces application behind Keycloak SSO. One fetch needs
four requests: load the page, switch to quarter-hour values, show the chosen
period and download that result as CSV. The CSV covers any date range at once.
"""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime
from html import unescape
import io
import logging
import re
from typing import Literal
from zoneinfo import ZoneInfo

from aiohttp import ClientError, ClientResponse, ClientResponseError, ClientSession, ClientTimeout

PORTAL_URL = "https://services.linznetz.at/verbrauchsdateninformation/consumption.jsf"
VIENNA = ZoneInfo("Europe/Vienna")

_TIMEOUT = ClientTimeout(total=60)
_FORM = "myForm1"
_SHOW_BUTTON = f"{_FORM}:btnIdA1"
_AJAX_HEADERS = {"Faces-Request": "partial/ajax", "X-Requested-With": "XMLHttpRequest"}

_LOGIN_FORM_RE = re.compile(r'<form[^>]*action="([^"]*/login-actions/authenticate[^"]*)"')
_VIEW_STATE_RE = re.compile(
    r'name="jakarta\.faces\.ViewState"[^>]*value="([^"]+)"'
    r"|ViewState:0\"><!\[CDATA\[([^\]]+)\]\]>"
)
_KIND_FIELD_RE = re.compile(r'name="(myForm1:[^"]*grid_eval:selectedClass)"')
_UNIT_FIELD_RE = re.compile(r'name="(myForm1:[^"]*:selectedClass)"[^>]*value="KWH"')
_EXPORT_RE = re.compile(r'id="(myForm1:exportAreaID:[^"]+)"')
_CSV_HEADER = ["Datum von", "Datum bis", "Energiemenge in kWh"]
_LOGGER = logging.getLogger(__name__)
_RequestStep = Literal["open", "login", "switch", "show", "export"]
_CONTENT_TYPES = {
    "text/html", "application/xhtml+xml", "text/xml", "application/xml",
    "text/csv", "application/csv", "text/plain", "application/octet-stream",
}


class LinzNetzError(Exception):
    """The portal no longer works as expected, most likely after a redesign.

    ``code`` names the failing step with a stable, non-sensitive identifier
    (for example ``export-link``); SmartStamm uses it to file GitHub issues.
    """

    kind = "portal_changed"

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class LinzNetzAuthError(LinzNetzError):
    """The portal rejected the credentials."""

    kind = "login_rejected"


class LinzNetzConnectionError(LinzNetzError):
    """Network trouble, timeouts or server errors; usually temporary."""

    kind = "connection_error"


class LinzNetzClient:
    """Fetches quarter-hour consumption as CSV text."""

    def __init__(self, session: ClientSession, username: str, password: str) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._step: _RequestStep = "open"
        self._response_summary: str | None = None

    async def async_fetch_csv(self, first_day: date, last_day: date) -> str:
        """Return the portal CSV export for both days inclusive."""
        self._step = "open"
        self._response_summary = None
        try:
            return await self._async_fetch_csv(first_day, last_day)
        except LinzNetzError as err:
            # Only fixed labels, numbers and presence flags. Never log the
            # exception text, URLs, headers, cookies or portal/CSV contents.
            _LOGGER.warning(
                "Portal fetch failed: code=%s step=%s response={%s}",
                err.code, self._step, self._response_summary or "unavailable",
            )
            raise

    async def _async_fetch_csv(self, first_day: date, last_day: date) -> str:
        try:
            page = await self._async_open_portal()
            view_state = _view_state(page)
            kind_field = _find(_KIND_FIELD_RE, page, "kind-field", "Auswahl Viertelstundenwerte")

            switched = await self._async_ajax(
                view_state,
                step="switch",
                source=kind_field,
                execute=kind_field,
                render=_FORM,
                event="change",
                fields={kind_field: "ConsumQuarter"},
            )
            view_state = _view_state(switched, view_state)
            fields = {
                kind_field: "ConsumQuarter",
                _find(_UNIT_FIELD_RE, switched, "unit-field", "Einheit kWh"): "KWH",
                f"{_FORM}:calendarFromRegion": first_day.strftime("%d.%m.%Y"),
                f"{_FORM}:calendarToRegion": last_day.strftime("%d.%m.%Y"),
            }

            # The export always delivers the last result shown, so show it first.
            shown = await self._async_ajax(
                view_state,
                step="show",
                source=_SHOW_BUTTON,
                execute=_FORM,
                render=f"{_FORM}:list",
                fields={**fields, _SHOW_BUTTON: _SHOW_BUTTON},
            )
            view_state = _view_state(shown, view_state)
            # The first export link is CSV; the others are XML and MSCONS.
            export = _find(_EXPORT_RE, shown, "export-link", "CSV-Export")

            self._step = "export"
            self._response_summary = None
            async with self._session.post(
                PORTAL_URL,
                data={_FORM: _FORM, "jakarta.faces.ViewState": view_state, **fields, export: export},
                timeout=_TIMEOUT,
            ) as response:
                self._response_summary = _response_summary(response)
                response.raise_for_status()
                if ".csv" not in response.headers.get("Content-Disposition", ""):
                    raise LinzNetzError("not-csv", "Portal lieferte keine CSV-Datei")
                return await self._async_read_response(response, encoding="utf-8")
        except ClientResponseError as err:
            # A missing page or form points to a redesign, server errors do not.
            if err.status < 500:
                raise LinzNetzError(f"http-{err.status}", f"Portal antwortet mit HTTP {err.status}") from err
            raise LinzNetzConnectionError("connection", f"Portal antwortet mit HTTP {err.status}") from err
        except (ClientError, TimeoutError) as err:
            raise LinzNetzConnectionError("connection", "Verbindung zum Portal fehlgeschlagen") from err

    async def _async_open_portal(self) -> str:
        """Load the consumption page and sign in first when the session expired."""
        async with self._session.get(PORTAL_URL, timeout=_TIMEOUT) as response:
            page = await self._async_read_response(response)
        login = _LOGIN_FORM_RE.search(page)
        if login is None:
            return page

        self._step = "login"
        self._response_summary = None
        async with self._session.post(
            unescape(login.group(1)),
            data={"username": self._username, "password": self._password},
            timeout=_TIMEOUT,
        ) as response:
            page = await self._async_read_response(response)
        if _LOGIN_FORM_RE.search(page):
            raise LinzNetzAuthError("login-rejected", "Anmeldung abgelehnt")
        return page

    async def _async_ajax(
        self,
        view_state: str,
        *,
        step: Literal["switch", "show"],
        source: str,
        execute: str,
        render: str,
        fields: dict[str, str],
        event: str | None = None,
    ) -> str:
        data = {
            _FORM: _FORM,
            "jakarta.faces.ViewState": view_state,
            "jakarta.faces.partial.ajax": "true",
            "jakarta.faces.source": source,
            "jakarta.faces.partial.execute": execute,
            "jakarta.faces.partial.render": render,
            **fields,
        }
        if event is not None:
            data["jakarta.faces.behavior.event"] = event
        self._step = step
        self._response_summary = None
        async with self._session.post(
            PORTAL_URL, data=data, headers=_AJAX_HEADERS, timeout=_TIMEOUT
        ) as response:
            return await self._async_read_response(response)

    async def _async_read_response(
        self, response: ClientResponse, *, encoding: str | None = None
    ) -> str:
        """Keep safe metadata even if the status check or body read fails."""
        self._response_summary = _response_summary(response)
        response.raise_for_status()
        text = await response.text(encoding=encoding)
        self._response_summary = _response_summary(response, text)
        return text


def _response_summary(response: ClientResponse, text: str | None = None) -> str:
    """Describe the response using an allowlist, never response-owned strings."""
    content_type = response.content_type
    if content_type not in _CONTENT_TYPES:
        content_type = "other"
    target = "other"
    if response.url.host == "sso.linznetz.at":
        target = "sso"
    elif (
        response.url.host == "services.linznetz.at"
        and response.url.path == "/verbrauchsdateninformation/consumption.jsf"
    ):
        target = "consumption"
    summary = (
        f"http_status={response.status} content_type={content_type} "
        f"target={target} redirects={len(response.history)} body_read={text is not None}"
    )
    if text is None:
        return summary
    return (
        f"{summary} body_chars={len(text)} "
        f"login_form={_LOGIN_FORM_RE.search(text) is not None} "
        f"view_state={_VIEW_STATE_RE.search(text) is not None} "
        f"kind_field={_KIND_FIELD_RE.search(text) is not None} "
        f"unit_field={_UNIT_FIELD_RE.search(text) is not None} "
        f"export_link={_EXPORT_RE.search(text) is not None} "
        f"partial_response={re.search(r'<partial-response(?:\s|>)', text) is not None}"
    )


def _find(pattern: re.Pattern[str], text: str, code: str, what: str) -> str:
    match = pattern.search(text)
    if match is None:
        raise LinzNetzError(code, f"Portal hat sich geändert: {what} nicht gefunden")
    return match.group(1)


def _view_state(text: str, fallback: str | None = None) -> str:
    match = _VIEW_STATE_RE.search(text)
    if match is not None:
        return match.group(1) or match.group(2)
    if fallback is None:
        raise LinzNetzError("view-state", "Portal hat sich geändert: ViewState nicht gefunden")
    return fallback


def parse_quarter_hours(text: str) -> dict[datetime, float]:
    """Map the UTC start of every quarter hour in the CSV export to its kWh.

    The CSV uses Vienna local time without offset. In the October DST change
    the hour from 02:00 repeats; a start that does not move forward in UTC
    therefore belongs to the second pass (``fold=1``).
    """
    readings: dict[datetime, float] = {}
    previous: datetime | None = None
    rows = csv.reader(io.StringIO(text.lstrip("\ufeff")), delimiter=";")
    if next(rows, [])[:3] != _CSV_HEADER:
        raise LinzNetzError("csv-format", "CSV-Export hat andere Spalten")
    for row in rows:
        if len(row) < 3 or not row[2]:
            continue
        try:
            local = datetime.strptime(row[0], "%d.%m.%Y %H:%M")
            kwh = float(row[2].replace(",", "."))
        except ValueError as err:
            raise LinzNetzError("csv-format", "CSV-Export hat ein anderes Format") from err
        start = local.replace(tzinfo=VIENNA).astimezone(UTC)
        if previous is not None and start <= previous:
            start = local.replace(tzinfo=VIENNA, fold=1).astimezone(UTC)
        readings[start] = kwh
        previous = start
    return readings


def complete_hours(readings: dict[datetime, float]) -> list[tuple[datetime, float]]:
    """Sum quarter hours into hours, keeping only hours with all four values."""
    hours: dict[datetime, list[float]] = {}
    for start, kwh in readings.items():
        hours.setdefault(start.replace(minute=0), []).append(kwh)
    return [
        (hour, round(sum(values), 3))
        for hour, values in sorted(hours.items())
        if len(values) == 4
    ]
