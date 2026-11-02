"""Errori applicativi con codice di uscita e indicazione operativa.

Ogni errore che arriva alla CLI porta:
- ``code``: identificativo stabile (usato nell'output --json e dalla futura API)
- ``exit_code``: codice di uscita del processo (vedi docs/commands.md)
- ``hint``: cosa fare per risolvere
"""

from __future__ import annotations


class ExitCode:
    OK = 0
    ERROR = 1
    USAGE = 2
    NOT_FOUND = 3
    CONFLICT = 4
    AUTH_REQUIRED = 5
    SERVICE_UNAVAILABLE = 6
    REMOTE_ERROR = 7
    NOT_READY = 8
    DEVICE_ERROR = 9
    STORAGE_ERROR = 10


class AgencyError(Exception):
    code = "ERROR"
    exit_code = ExitCode.ERROR

    def __init__(self, message: str, *, hint: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details or {}

    def to_dict(self) -> dict:
        data = {"code": self.code, "message": self.message}
        if self.hint:
            data["hint"] = self.hint
        if self.details:
            data["details"] = self.details
        return data


class UsageError(AgencyError):
    code = "USAGE"
    exit_code = ExitCode.USAGE


class NotFound(AgencyError):
    code = "NOT_FOUND"
    exit_code = ExitCode.NOT_FOUND


class Conflict(AgencyError):
    code = "CONFLICT"
    exit_code = ExitCode.CONFLICT


class ServiceUnavailable(AgencyError):
    code = "SERVICE_UNAVAILABLE"
    exit_code = ExitCode.SERVICE_UNAVAILABLE


class NotReady(AgencyError):
    code = "NOT_READY"
    exit_code = ExitCode.NOT_READY


class DeviceError(AgencyError):
    code = "DEVICE_ERROR"
    exit_code = ExitCode.DEVICE_ERROR


class StorageError(AgencyError):
    code = "STORAGE_ERROR"
    exit_code = ExitCode.STORAGE_ERROR


class ConfigError(AgencyError):
    code = "CONFIG_ERROR"
    exit_code = ExitCode.USAGE


# --- Errori del backend NotebookLM (neutrali rispetto alla libreria) ---------


class RemoteError(AgencyError):
    """Errore generico del servizio remoto."""

    code = "REMOTE_ERROR"
    exit_code = ExitCode.REMOTE_ERROR
    retryable = False


class AuthRequired(RemoteError):
    code = "AUTH_REQUIRED"
    exit_code = ExitCode.AUTH_REQUIRED

    def __init__(self, message: str = "Sessione NotebookLM assente o scaduta", **kw):
        kw.setdefault(
            "hint",
            "Esegui il login sul Mac e trasferisci la sessione: vedi 'agency notebook login'.",
        )
        super().__init__(message, **kw)


class TransientRemoteError(RemoteError):
    """Rete assente, timeout prima dell'invio, errore 5xx: si può ritentare."""

    code = "REMOTE_TRANSIENT"
    retryable = True


class RateLimited(TransientRemoteError):
    code = "RATE_LIMITED"

    def __init__(self, message: str, retry_after: float | None = None, **kw):
        super().__init__(message, **kw)
        self.retry_after = retry_after


class UncertainUpload(RemoteError):
    """L'upload potrebbe essere avvenuto: serve riconciliazione, non un nuovo invio."""

    code = "UPLOAD_UNCERTAIN"

    def __init__(self, message: str, source_id: str | None = None, **kw):
        super().__init__(message, **kw)
        self.source_id = source_id


class RemoteNotFound(RemoteError):
    code = "REMOTE_NOT_FOUND"
    exit_code = ExitCode.NOT_FOUND


class PermanentRemoteError(RemoteError):
    code = "REMOTE_REJECTED"
