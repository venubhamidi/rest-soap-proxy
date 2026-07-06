"""Phase 1 verification: config loading, ${ENV} expansion, and validation."""
import textwrap

import pytest

from soap_mcp.config import (
    AppConfig,
    BasicAuth,
    ClientCertAuth,
    ConfigError,
    NoAuth,
    WsseUsernameAuth,
    load_config,
)


def write(tmp_path, body: str) -> str:
    p = tmp_path / "services.yaml"
    p.write_text(textwrap.dedent(body))
    return str(p)


def test_valid_config_loads_all_auth_types(tmp_path, monkeypatch):
    monkeypatch.setenv("WEATHER_PW", "s3cret")
    monkeypatch.setenv("CLAIMS_USER", "claims-svc")
    path = write(
        tmp_path,
        """
        settings:
          wsdl_request_timeout: 15
          operation_timeout: 45
          hot_reload: false
        services:
          - name: weather
            wsdl: ./wsdls/weather.wsdl
            wsdl_service: WeatherService
            wsdl_port: WeatherPort
            auth:
              type: wsse_username
              username: svc-weather
              password: ${WEATHER_PW}
              use_digest: false
              add_timestamp: true
            headers:
              X-Route: internal
          - name: claims
            wsdl: https://internal.example.com/claims?wsdl
            auth:
              type: basic
              username: ${CLAIMS_USER}
              password: hunter2
          - name: legacy
            wsdl: ./wsdls/legacy.wsdl
            auth:
              type: client_cert
              cert_path: /etc/certs/client.pem
              key_path: /etc/certs/client.key
          - name: open
            wsdl: ./wsdls/open.wsdl
        """,
    )
    cfg = load_config(path)
    assert isinstance(cfg, AppConfig)
    assert cfg.settings.wsdl_request_timeout == 15
    assert cfg.settings.hot_reload is False

    by_name = {s.name: s for s in cfg.services}
    assert isinstance(by_name["weather"].auth, WsseUsernameAuth)
    assert by_name["weather"].auth.password == "s3cret"  # expanded
    assert by_name["weather"].headers == {"X-Route": "internal"}
    assert isinstance(by_name["claims"].auth, BasicAuth)
    assert by_name["claims"].auth.username == "claims-svc"  # expanded
    assert isinstance(by_name["legacy"].auth, ClientCertAuth)
    assert isinstance(by_name["open"].auth, NoAuth)  # default


def test_defaults_applied(tmp_path):
    path = write(
        tmp_path,
        """
        services:
          - name: svc
            wsdl: ./a.wsdl
        """,
    )
    cfg = load_config(path)
    assert cfg.settings.wsdl_request_timeout == 30
    assert cfg.settings.operation_timeout == 60
    assert cfg.settings.hot_reload is True


def test_unset_env_var_is_error_naming_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("MISSING_PW", raising=False)
    path = write(
        tmp_path,
        """
        services:
          - name: svc
            wsdl: ./a.wsdl
            auth:
              type: basic
              username: u
              password: ${MISSING_PW}
        """,
    )
    with pytest.raises(ConfigError) as exc:
        load_config(path)
    assert "MISSING_PW" in str(exc.value)


def test_duplicate_service_name_is_error(tmp_path):
    path = write(
        tmp_path,
        """
        services:
          - name: dup
            wsdl: ./a.wsdl
          - name: dup
            wsdl: ./b.wsdl
        """,
    )
    with pytest.raises(ConfigError) as exc:
        load_config(path)
    assert "dup" in str(exc.value)


def test_bad_auth_type_is_error(tmp_path):
    path = write(
        tmp_path,
        """
        services:
          - name: svc
            wsdl: ./a.wsdl
            auth:
              type: kerberos
              username: u
        """,
    )
    with pytest.raises(ConfigError):
        load_config(path)


def test_invalid_service_name_is_error(tmp_path):
    path = write(
        tmp_path,
        """
        services:
          - name: Bad Name!
            wsdl: ./a.wsdl
        """,
    )
    with pytest.raises(ConfigError) as exc:
        load_config(path)
    assert "Bad Name!" in str(exc.value) or "invalid" in str(exc.value).lower()


def test_missing_file_is_error(tmp_path):
    with pytest.raises(ConfigError) as exc:
        load_config(str(tmp_path / "nope.yaml"))
    assert "not found" in str(exc.value)


def test_missing_required_auth_field_is_error(tmp_path):
    path = write(
        tmp_path,
        """
        services:
          - name: svc
            wsdl: ./a.wsdl
            auth:
              type: basic
              username: u
        """,
    )
    with pytest.raises(ConfigError):
        load_config(path)  # password missing
