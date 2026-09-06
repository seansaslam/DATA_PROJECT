"""Application metadata.

Django's own database is a local SQLite file. It holds the connection profiles
and the change-feed bookkeeping -- watermarks, load-run history, simulator
activity -- deliberately, so that nothing this tool does leaves control tables
inside the three source databases. A data engineer pointing ADF or Fivetran at
WELL_MASTER_DB should see the well master and nothing else.

The source databases themselves are reached through raw pyodbc, because their
DDL is vendor-shaped, has no foreign keys, and must stay byte-identical to the
SQL a data engineer would be handed.
"""
from __future__ import annotations

from django.db import models

from . import dialects, registry


class ConnectionProfile(models.Model):
    """Where one application database lives, and what kind of engine it is."""

    system_key = models.CharField(max_length=20, unique=True)
    db_type = models.CharField(max_length=20, default="sqlserver",
                               choices=dialects.DIALECT_CHOICES)
    server = models.CharField(max_length=200, default="")
    port = models.IntegerField(default=1433)
    database = models.CharField(max_length=128)
    username = models.CharField(max_length=128, default="")
    password = models.CharField(max_length=256, blank=True, default="")
    extra_options = models.CharField(max_length=400, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["system_key"]

    def __str__(self) -> str:
        return f"{self.system_key} -> {self.username}@{self.server}/{self.database}"

    # -- derived ------------------------------------------------------------
    @property
    def spec(self) -> registry.SystemSpec:
        return registry.system(self.system_key)

    @property
    def dialect(self) -> dialects.Dialect:
        return dialects.get(self.db_type)

    @property
    def label(self) -> str:
        return self.spec.label

    def connection_string(self, timeout: int = 15) -> str:
        return self.dialect.connection_string(
            server=self.server, database=self.database, username=self.username,
            password=self.password, port=self.port, timeout=timeout,
            extra=self.extra_options,
        )

    def describe(self) -> str:
        return f"{self.username}@{self.server}:{self.port}/{self.database}"


def ensure_defaults() -> None:
    """Create one profile per system on first run.

    Server, user and password come from the environment (see .env.example), not
    from source. After first run the profiles live in app_state.sqlite3 and are
    edited on the Connections page, so changing .env later does not overwrite
    what someone typed there.
    """
    from django.conf import settings

    for key, spec in registry.ALL_SYSTEMS.items():
        ConnectionProfile.objects.get_or_create(
            system_key=key,
            defaults={**settings.SOURCE_DB_DEFAULTS,
                      "database": spec.default_database},
        )


def profile(system_key: str) -> ConnectionProfile:
    ensure_defaults()
    return ConnectionProfile.objects.get(system_key=system_key)


class Watermark(models.Model):
    """Where a watermarked load got to, per table per strategy.

    One row per strategy on purpose: switching a system from CT to CDC and back
    must not have the two mechanisms overwriting each other's position.
    """

    system_key = models.CharField(max_length=20)
    source_table = models.CharField(max_length=128)
    strategy = models.CharField(max_length=12)
    ct_version = models.BigIntegerField(null=True, blank=True)
    cdc_lsn = models.CharField(max_length=40, null=True, blank=True)   # hex string
    high_water_ts = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("system_key", "source_table", "strategy")]
        ordering = ["system_key", "source_table", "strategy"]


class LoadRun(models.Model):
    """One strategy, one table, one moment -- the numbers you compare.

    The whole training exercise reduces to reading this table: the same churn,
    read four different ways, produces four very different ROWS_READ.
    """

    run_ts = models.DateTimeField(auto_now_add=True)
    system_key = models.CharField(max_length=20)
    source_table = models.CharField(max_length=128)
    strategy = models.CharField(max_length=12)
    rows_read = models.IntegerField(default=0)
    rows_upserted = models.IntegerField(default=0)
    rows_deleted = models.IntegerField(default=0)
    duration_ms = models.IntegerField(default=0)
    baseline = models.BooleanField(default=False)
    committed = models.BooleanField(default=False)
    status = models.CharField(max_length=12, default="OK")
    note = models.CharField(max_length=600, blank=True, default="")

    class Meta:
        ordering = ["-id"]


class ActivityLog(models.Model):
    """What the simulator did, so a churn cycle can be tied to a load run."""

    event_ts = models.DateTimeField(auto_now_add=True)
    system_key = models.CharField(max_length=20, blank=True, default="")
    action = models.CharField(max_length=40)
    detail = models.CharField(max_length=600, blank=True, default="")
    row_count = models.IntegerField(default=0)

    class Meta:
        ordering = ["-id"]


class SystemMode(models.Model):
    """The strategy each system is currently armed for."""

    system_key = models.CharField(max_length=20, unique=True)
    strategy = models.CharField(max_length=12, default="FULL")
    updated_at = models.DateTimeField(auto_now=True)
