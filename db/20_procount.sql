/* ============================================================================
   PROCOUNT_DB  --  schema [pc]
   Quorum / Merrick ProCount shaped production accounting source.

   ProCount does not key on the API. It keys on MERRICK_ID, its own surrogate
   for a completion, and carries the API as a plain attribute. Conforming
   MERRICK_ID -> API_UWI is the warehouse's job, not the source's.
   ============================================================================ */

IF SCHEMA_ID('pc') IS NULL EXEC('CREATE SCHEMA pc');
GO

/* -- completion / entity master ------------------------------------------- */
IF OBJECT_ID('pc.pc_completion') IS NULL
CREATE TABLE pc.pc_completion (
    MERRICK_ID          int             NOT NULL,   -- ProCount surrogate key
    API_UWI             varchar(14)     NOT NULL,   -- link value into wm.cct_well_master
    COMPLETION_NAME     varchar(100)    NOT NULL,
    PROD_ROUTE          varchar(40)     NULL,       -- pumper route
    BATTERY_ID          int             NULL,
    BATTERY_NAME        varchar(40)     NULL,       -- tank battery / central facility
    AREA                varchar(40)     NOT NULL,
    ACTIVE_FLAG         bit             NOT NULL,
    FIRST_PROD_DATE     date            NULL,
    ALLOC_METHOD        varchar(20)     NOT NULL,   -- TEST | METER | THEORETICAL
    CREATED_TS          datetime2(3)    NOT NULL CONSTRAINT DF_pcc_created DEFAULT SYSUTCDATETIME(),
    UPDATED_TS          datetime2(3)    NOT NULL CONSTRAINT DF_pcc_updated DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_pc_completion PRIMARY KEY CLUSTERED (MERRICK_ID)
);
GO

IF INDEXPROPERTY(OBJECT_ID('pc.pc_completion'), 'IX_pcc_api', 'IndexID') IS NULL
CREATE NONCLUSTERED INDEX IX_pcc_api ON pc.pc_completion (API_UWI);
GO

/* -- daily allocated production ------------------------------------------- */
IF OBJECT_ID('pc.well_daily_prod') IS NULL
CREATE TABLE pc.well_daily_prod (
    MERRICK_ID          int             NOT NULL,
    PROD_DATE           date            NOT NULL,
    API_UWI             varchar(14)     NOT NULL,
    OIL_BBL             decimal(12,2)   NOT NULL,   -- gross allocated volumes
    GAS_MCF             decimal(12,2)   NOT NULL,
    WATER_BBL           decimal(12,2)   NOT NULL,
    NGL_BBL             decimal(12,2)   NOT NULL,
    NET_OIL_BBL         decimal(12,2)   NOT NULL,   -- gross x NRI
    NET_GAS_MCF         decimal(12,2)   NOT NULL,
    NET_NGL_BBL         decimal(12,2)   NOT NULL,
    PRODUCING_HOURS     decimal(5,2)    NOT NULL,
    DOWNTIME_HOURS      decimal(5,2)    NOT NULL,
    TUBING_PRESSURE_PSI int             NULL,
    CASING_PRESSURE_PSI int             NULL,
    CHOKE_SIZE_64THS    int             NULL,
    WELL_STATUS_CODE    varchar(20)     NOT NULL,   -- PRODUCING | SHUT-IN | DOWN
    ALLOCATION_STATUS   varchar(20)     NOT NULL,   -- ESTIMATED | ALLOCATED | FINAL
    CREATED_TS          datetime2(3)    NOT NULL CONSTRAINT DF_pcd_created DEFAULT SYSUTCDATETIME(),
    UPDATED_TS          datetime2(3)    NOT NULL CONSTRAINT DF_pcd_updated DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_well_daily_prod PRIMARY KEY CLUSTERED (MERRICK_ID, PROD_DATE)
);
GO

IF INDEXPROPERTY(OBJECT_ID('pc.well_daily_prod'), 'IX_pcd_date', 'IndexID') IS NULL
CREATE NONCLUSTERED INDEX IX_pcd_date ON pc.well_daily_prod (PROD_DATE) INCLUDE (API_UWI, OIL_BBL, GAS_MCF);
GO

IF INDEXPROPERTY(OBJECT_ID('pc.well_daily_prod'), 'IX_pcd_updated', 'IndexID') IS NULL
CREATE NONCLUSTERED INDEX IX_pcd_updated ON pc.well_daily_prod (UPDATED_TS);
GO

/* -- downtime / deferment events ------------------------------------------ */
IF OBJECT_ID('pc.pc_daily_downtime') IS NULL
CREATE TABLE pc.pc_daily_downtime (
    MERRICK_ID          int             NOT NULL,
    DOWNTIME_DATE       date            NOT NULL,
    SEQ_NO              smallint        NOT NULL,
    API_UWI             varchar(14)     NOT NULL,
    DOWNTIME_HOURS      decimal(5,2)    NOT NULL,
    REASON_CODE         varchar(10)     NOT NULL,
    REASON_DESC         varchar(80)     NOT NULL,
    DEFERRED_OIL_BBL    decimal(12,2)   NOT NULL,
    DEFERRED_GAS_MCF    decimal(12,2)   NOT NULL,
    CREATED_TS          datetime2(3)    NOT NULL CONSTRAINT DF_pcdt_created DEFAULT SYSUTCDATETIME(),
    UPDATED_TS          datetime2(3)    NOT NULL CONSTRAINT DF_pcdt_updated DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_pc_daily_downtime PRIMARY KEY CLUSTERED (MERRICK_ID, DOWNTIME_DATE, SEQ_NO)
);
GO

IF INDEXPROPERTY(OBJECT_ID('pc.pc_daily_downtime'), 'IX_pcdt_updated', 'IndexID') IS NULL
CREATE NONCLUSTERED INDEX IX_pcdt_updated ON pc.pc_daily_downtime (UPDATED_TS);
GO
