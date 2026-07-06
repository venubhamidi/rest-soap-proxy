"""
Config loading and validation for the SOAP → MCP proxy.

Loads ``services.yaml``, expands ``${ENV_VAR}`` references from the environment,
and validates the result into typed pydantic models. Any problem — unset env
var, duplicate service name, bad auth type, malformed file — raises
``ConfigError`` with a message naming the specific problem.
"""
from __future__ import annotations

import os
import re
from typing import Annotated, Literal, Optional, Union

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

# ${VAR} or ${VAR_NAME} anywhere in a string. Names follow shell convention.
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Legal characters for a service name (also the prefix of every tool name).
_SERVICE_NAME_PATTERN = re.compile(r"^[a-z0-9_-]+$")


class ConfigError(Exception):
    """Raised for any invalid configuration. The message names the problem."""


# --- Auth models -----------------------------------------------------------


class NoAuth(BaseModel):
    type: Literal["none"] = "none"


class WsseUsernameAuth(BaseModel):
    type: Literal["wsse_username"]
    username: str
    password: str
    use_digest: bool = False
    add_timestamp: bool = True
    timestamp_ttl: int = 300


class BasicAuth(BaseModel):
    type: Literal["basic"]
    username: str
    password: str


class ClientCertAuth(BaseModel):
    type: Literal["client_cert"]
    cert_path: str
    key_path: Optional[str] = None
    ca_bundle_path: Optional[str] = None


AuthConfig = Annotated[
    Union[NoAuth, WsseUsernameAuth, BasicAuth, ClientCertAuth],
    Field(discriminator="type"),
]


# --- Service / settings / app models ---------------------------------------


class Settings(BaseModel):
    wsdl_request_timeout: int = 30
    operation_timeout: int = 60
    hot_reload: bool = True


class ServiceConfig(BaseModel):
    name: str
    wsdl: str
    wsdl_service: Optional[str] = None
    wsdl_port: Optional[str] = None
    auth: AuthConfig = Field(default_factory=NoAuth)
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not _SERVICE_NAME_PATTERN.match(v):
            raise ValueError(
                f"service name '{v}' is invalid; use only [a-z0-9_-]"
            )
        return v


class AppConfig(BaseModel):
    settings: Settings = Field(default_factory=Settings)
    services: list[ServiceConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_names(self) -> "AppConfig":
        seen: set[str] = set()
        for svc in self.services:
            if svc.name in seen:
                raise ValueError(f"duplicate service name '{svc.name}'")
            seen.add(svc.name)
        return self


# --- Loading ---------------------------------------------------------------


def _expand_env(value, path: str = ""):
    """Recursively expand ``${VAR}`` in strings. Unset var → ConfigError."""
    if isinstance(value, str):
        def _sub(match: re.Match) -> str:
            var = match.group(1)
            if var not in os.environ:
                where = f" (at {path})" if path else ""
                raise ConfigError(
                    f"environment variable '{var}' is referenced in config"
                    f"{where} but is not set"
                )
            return os.environ[var]

        return _ENV_PATTERN.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _expand_env(v, f"{path}.{k}" if path else str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v, f"{path}[{i}]") for i, v in enumerate(value)]
    return value


def load_config(path: str) -> AppConfig:
    """Load and validate ``services.yaml``. Raises ``ConfigError`` on any problem."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError as e:
        raise ConfigError(f"config file not found: {path}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"config file is not valid YAML: {e}") from e

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")

    raw = _expand_env(raw)

    try:
        return AppConfig.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(_format_validation_error(e)) from e


def _format_validation_error(e: ValidationError) -> str:
    """Turn a pydantic ValidationError into a concise, problem-naming message."""
    parts = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "<root>"
        parts.append(f"{loc}: {err['msg']}")
    return "invalid config — " + "; ".join(parts)
