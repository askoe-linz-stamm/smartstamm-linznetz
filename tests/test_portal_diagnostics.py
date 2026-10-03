"""Failures describe the response without copying private portal contents."""

from datetime import date
import logging
from unittest.mock import AsyncMock, MagicMock

from aiohttp import ClientConnectionError, ClientResponse, ClientResponseError, ClientSession
import pytest
from yarl import URL

from custom_components.linznetz.api import LinzNetzClient, LinzNetzError, PORTAL_URL

VIEW_STATE = '<input name="jakarta.faces.ViewState" value="PRIVATE_VIEW_STATE">'
KIND = '<input name="myForm1:grid_eval:selectedClass" value="ConsumQuarter">'
UNIT = '<input name="myForm1:unit:selectedClass" value="KWH">'
EXPORT = '<a id="myForm1:exportAreaID:csv">CSV</a>'
PAGE = VIEW_STATE + KIND
SWITCHED = '<partial-response><update><![CDATA[' + UNIT + ']]></update></partial-response>'
SHOWN = '<partial-response><update><![CDATA[' + EXPORT + ']]></update></partial-response>'
LOGIN = '<form action="https://sso.linznetz.at/login-actions/authenticate?session=PRIVATE_SESSION">'
CSV = "Datum von;Datum bis;Energiemenge in kWh\n02.10.2026 00:00;02.10.2026 00:15;PRIVATE_READING"


def response(
    text: str, *, status: int = 200, content_type: str = "text/html", url: str = PORTAL_URL
) -> MagicMock:
    result = MagicMock(spec=ClientResponse)
    result.status = status
    result.content_type = content_type
    result.url = URL(url)
    result.history = ()
    result.headers = {}
    result.text = AsyncMock(return_value=text)
    if status >= 400:
        result.raise_for_status.side_effect = ClientResponseError(
            MagicMock(), (), status=status, message="PRIVATE_HTTP_ERROR"
        )
    return result


def client_with_responses(*responses: MagicMock) -> tuple[LinzNetzClient, MagicMock]:
    session = MagicMock(spec=ClientSession)
    session.get.return_value.__aenter__ = AsyncMock(return_value=responses[0])
    session.post.return_value.__aenter__ = AsyncMock(side_effect=responses[1:])
    return LinzNetzClient(session, "PRIVATE_USERNAME", "PRIVATE_PASSWORD"), session


@pytest.mark.parametrize(
    ("responses", "code", "step", "features"),
    [
        ([response("PRIVATE_PORTAL_TEXT")], "view-state", "open", ["view_state=False"]),
        ([response(VIEW_STATE + "PRIVATE_PORTAL_TEXT")], "kind-field", "open",
         ["view_state=True", "kind_field=False"]),
        ([response(PAGE), response('<partial-response>PRIVATE_PORTAL_TEXT</partial-response>')],
         "unit-field", "switch", ["partial_response=True", "unit_field=False"]),
        ([response(PAGE), response(SWITCHED), response("PRIVATE_PORTAL_TEXT")],
         "export-link", "show", ["export_link=False"]),
        ([response(PAGE), response(SWITCHED), response(SHOWN), response("PRIVATE_PORTAL_TEXT")],
         "not-csv", "export", ["body_read=False"]),
        ([response(LOGIN), response(LOGIN, url="https://sso.linznetz.at/login-actions/authenticate?secret=PRIVATE_URL")],
         "login-rejected", "login", ["target=sso", "login_form=True"]),
        ([response("PRIVATE_PORTAL_TEXT", status=503)], "connection", "open",
         ["http_status=503", "body_read=False"]),
    ],
)
async def test_failed_fetch_logs_safe_response_features(
    caplog: pytest.LogCaptureFixture, responses: list[MagicMock], code: str,
    step: str, features: list[str],
) -> None:
    client, _ = client_with_responses(*responses)

    with caplog.at_level(logging.WARNING, logger="custom_components.linznetz.api"):
        with pytest.raises(LinzNetzError) as error:
            await client.async_fetch_csv(date(2026, 10, 1), date(2026, 10, 2))

    assert error.value.code == code
    assert len(caplog.records) == 1
    assert f"code={code} step={step}" in caplog.text
    assert all(feature in caplog.text for feature in features)
    assert "PRIVATE_" not in caplog.text


async def test_response_owned_strings_are_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    page = response(
        VIEW_STATE, content_type="application/PRIVATE_CONTENT_TYPE",
        url="https://PRIVATE_HOST.invalid/PRIVATE_PATH?token=PRIVATE_QUERY",
    )
    page.history = (MagicMock(), MagicMock())
    page.headers = {"Set-Cookie": "PRIVATE_COOKIE", "X-Message": "PRIVATE_HEADER"}
    client, _ = client_with_responses(page)

    with pytest.raises(LinzNetzError):
        await client.async_fetch_csv(date(2026, 10, 1), date(2026, 10, 2))

    assert "content_type=other target=other redirects=2" in caplog.text
    assert "PRIVATE_" not in caplog.text


async def test_success_is_silent_and_next_connection_failure_has_no_stale_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    exported = response(CSV, content_type="text/csv")
    exported.headers = {"Content-Disposition": 'attachment; filename="PRIVATE_FILENAME.csv"'}
    client, session = client_with_responses(response(PAGE), response(SWITCHED), response(SHOWN), exported)

    assert await client.async_fetch_csv(date(2026, 10, 1), date(2026, 10, 2)) == CSV
    assert not caplog.records
    session.get.side_effect = ClientConnectionError("PRIVATE_CONNECTION_ERROR")

    with pytest.raises(LinzNetzError) as error:
        await client.async_fetch_csv(date(2026, 10, 1), date(2026, 10, 2))

    assert error.value.code == "connection"
    assert "code=connection step=open response={unavailable}" in caplog.text
    assert "PRIVATE_" not in caplog.text


async def test_request_timeout_does_not_reuse_previous_step_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, session = client_with_responses(response(PAGE))
    session.post.side_effect = TimeoutError("PRIVATE_TIMEOUT")

    with pytest.raises(LinzNetzError):
        await client.async_fetch_csv(date(2026, 10, 1), date(2026, 10, 2))

    assert "step=switch response={unavailable}" in caplog.text
    assert "PRIVATE_" not in caplog.text


async def test_body_timeout_keeps_only_current_response_metadata(
    caplog: pytest.LogCaptureFixture,
) -> None:
    switched = response("", content_type="application/xml")
    switched.text.side_effect = TimeoutError("PRIVATE_TIMEOUT")
    client, _ = client_with_responses(response(PAGE), switched)

    with pytest.raises(LinzNetzError):
        await client.async_fetch_csv(date(2026, 10, 1), date(2026, 10, 2))

    assert "step=switch" in caplog.text
    assert "content_type=application/xml" in caplog.text
    assert "body_read=False" in caplog.text
    assert "kind_field=" not in caplog.text
    assert "PRIVATE_" not in caplog.text
