"""Database-type abstraction.

Each application database picks its own type from the Connections page, so the
Well Master can sit on the local SQL Server while ProCount runs on Azure SQL.
A dialect owns three things: how to build a connection string, what the engine
can do (change tracking, CDC), and the handful of statements whose syntax is not
shared.

Adding a type means adding one Dialect instance to DIALECTS. Nothing else in the
codebase branches on connection type.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Dialect:
    key: str
    label: str
    driver: str                     # ODBC driver name
    default_port: int
    encrypt: str                    # ODBC Encrypt=
    trust_cert: str                 # ODBC TrustServerCertificate=
    supports_change_tracking: bool
    supports_cdc: bool
    cdc_needs_sysadmin: bool        # can db_owner run sp_cdc_enable_db?
    server_hint: str = ""
    notes: str = ""

    # -- connection ---------------------------------------------------------
    def connection_string(self, *, server: str, database: str, username: str,
                          password: str, port: int | None = None,
                          timeout: int = 15, extra: str = "") -> str:
        host = server if not port or port == self.default_port else f"{server},{port}"
        parts = [
            f"DRIVER={{{self.driver}}}",
            f"SERVER={host}",
            f"DATABASE={database}",
            f"UID={username}",
            f"PWD={password}",
            f"Encrypt={self.encrypt}",
            f"TrustServerCertificate={self.trust_cert}",
            f"Connection Timeout={timeout}",
        ]
        if extra:
            parts.append(extra.strip().strip(";"))
        return ";".join(parts)

    # -- capability summary shown in the UI ---------------------------------
    def capabilities(self) -> list[tuple[str, bool]]:
        return [
            ("Full reload", True),
            ("Incremental (watermark)", True),
            ("Change Tracking", self.supports_change_tracking),
            ("Change Data Capture", self.supports_cdc),
        ]


SQLSERVER = Dialect(
    key="sqlserver",
    label="SQL Server (on-prem / VM)",
    driver="ODBC Driver 18 for SQL Server",
    default_port=1433,
    encrypt="yes",
    trust_cert="yes",
    supports_change_tracking=True,
    supports_cdc=True,
    cdc_needs_sysadmin=True,
    server_hint="sqlserver.example.internal",
    notes=("CDC needs a one-time 'EXEC sys.sp_cdc_enable_db' from a sysadmin, "
           "plus a running SQL Server Agent to populate the change tables."),
)

AZURE_SQL = Dialect(
    key="azure_sql",
    label="Azure SQL Database",
    driver="ODBC Driver 18 for SQL Server",
    default_port=1433,
    encrypt="yes",
    trust_cert="no",
    supports_change_tracking=True,
    supports_cdc=True,
    cdc_needs_sysadmin=False,
    server_hint="yourserver.database.windows.net",
    notes=("Azure SQL lets db_owner enable CDC itself -- no sysadmin, no Agent; "
           "the capture runs as a managed background process. Needs S3/HS or above."),
)

AZURE_SQL_MI = Dialect(
    key="azure_sql_mi",
    label="Azure SQL Managed Instance",
    driver="ODBC Driver 18 for SQL Server",
    default_port=1433,
    encrypt="yes",
    trust_cert="no",
    supports_change_tracking=True,
    supports_cdc=True,
    cdc_needs_sysadmin=True,
    server_hint="yourmi.public.xxxx.database.windows.net",
    notes="Behaves like a full SQL Server instance, Agent included.",
)

DIALECTS: dict[str, Dialect] = {d.key: d for d in (SQLSERVER, AZURE_SQL, AZURE_SQL_MI)}

DIALECT_CHOICES = [(d.key, d.label) for d in DIALECTS.values()]


def get(key: str) -> Dialect:
    try:
        return DIALECTS[key]
    except KeyError:
        raise ValueError(f"unknown database type {key!r}; "
                         f"known types: {', '.join(DIALECTS)}") from None
