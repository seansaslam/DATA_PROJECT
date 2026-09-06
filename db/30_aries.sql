/* ============================================================================
   ARIES_DB  --  schema [ac]
   Landmark / Halliburton ARIES shaped reserves + forecast source.

   ARIES keys everything on PROPNUM, a 10-character property id. AC_PROPERTY is
   the property master and denormalises most of the well header; AC_DAILY is the
   daily forecast stream. Only CCT-operated wells are forecast.
   ============================================================================ */

IF SCHEMA_ID('ac') IS NULL EXEC('CREATE SCHEMA ac');
GO

/* -- property master ------------------------------------------------------- */
IF OBJECT_ID('ac.AC_PROPERTY') IS NULL
CREATE TABLE ac.AC_PROPERTY (
    PROPNUM                 char(10)        NOT NULL,   -- ARIES property id
    API_UWI                 varchar(14)     NOT NULL,   -- link value into wm.cct_well_master
    WELL_NAME               varchar(100)    NOT NULL,
    LEASE_NAME              varchar(60)     NULL,
    OPERATOR_NAME           varchar(80)     NOT NULL,
    AREA                    varchar(40)     NOT NULL,
    PAD_NAME                varchar(40)     NULL,
    FIELD_NAME              varchar(60)     NULL,
    COUNTY                  varchar(40)     NOT NULL,
    STATE_CODE              char(2)         NOT NULL,
    RESERVOIR               varchar(40)     NULL,
    MAJOR_PHASE             varchar(10)     NOT NULL,   -- OIL | GAS
    WELL_STATUS             varchar(20)     NOT NULL,
    FIRST_PROD_DATE         date            NULL,
    SURFACE_LATITUDE        decimal(9,6)    NULL,
    SURFACE_LONGITUDE       decimal(9,6)    NULL,
    WORKING_INTEREST        decimal(9,6)    NOT NULL,
    NET_REVENUE_INTEREST    decimal(9,6)    NOT NULL,
    SCENARIO                varchar(20)     NOT NULL,   -- forecast case name
    RESERVE_CAT             varchar(10)     NOT NULL,   -- PDP | PDNP | PUD
    TYPE_CURVE              varchar(30)     NULL,
    EFFECTIVE_DATE          date            NOT NULL,
    QI_OIL_BOPD             decimal(12,2)   NULL,       -- Arps initial rate
    DI_NOMINAL              decimal(9,6)    NULL,       -- initial nominal decline, annual
    B_FACTOR                decimal(6,4)    NULL,       -- hyperbolic exponent
    CREATED_TS              datetime2(3)    NOT NULL CONSTRAINT DF_acp_created DEFAULT SYSUTCDATETIME(),
    UPDATED_TS              datetime2(3)    NOT NULL CONSTRAINT DF_acp_updated DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_AC_PROPERTY PRIMARY KEY CLUSTERED (PROPNUM)
);
GO

IF INDEXPROPERTY(OBJECT_ID('ac.AC_PROPERTY'), 'IX_acp_api', 'IndexID') IS NULL
CREATE NONCLUSTERED INDEX IX_acp_api ON ac.AC_PROPERTY (API_UWI);
GO

IF INDEXPROPERTY(OBJECT_ID('ac.AC_PROPERTY'), 'IX_acp_updated', 'IndexID') IS NULL
CREATE NONCLUSTERED INDEX IX_acp_updated ON ac.AC_PROPERTY (UPDATED_TS);
GO

/* -- daily forecast -------------------------------------------------------- */
IF OBJECT_ID('ac.AC_DAILY') IS NULL
CREATE TABLE ac.AC_DAILY (
    PROPNUM             char(10)        NOT NULL,
    D_DATE              date            NOT NULL,
    API_UWI             varchar(14)     NOT NULL,
    WELL_NAME           varchar(100)    NOT NULL,
    AREA                varchar(40)     NOT NULL,
    PAD_NAME            varchar(40)     NULL,
    WELL_STATUS         varchar(20)     NOT NULL,
    MAJOR_PHASE         varchar(10)     NOT NULL,
    GROSS_OIL_BBL       decimal(12,2)   NOT NULL,
    GROSS_GAS_MCF       decimal(12,2)   NOT NULL,
    GROSS_WATER_BBL     decimal(12,2)   NOT NULL,
    GROSS_NGL_BBL       decimal(12,2)   NOT NULL,
    NET_OIL_BBL         decimal(12,2)   NOT NULL,
    NET_GAS_MCF         decimal(12,2)   NOT NULL,
    NET_NGL_BBL         decimal(12,2)   NOT NULL,
    SCENARIO            varchar(20)     NOT NULL,
    RESERVE_CAT         varchar(10)     NOT NULL,
    FORECAST_CASE       varchar(20)     NOT NULL,   -- BASE | HIGH | LOW
    CREATED_TS          datetime2(3)    NOT NULL CONSTRAINT DF_acd_created DEFAULT SYSUTCDATETIME(),
    UPDATED_TS          datetime2(3)    NOT NULL CONSTRAINT DF_acd_updated DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_AC_DAILY PRIMARY KEY CLUSTERED (PROPNUM, D_DATE)
);
GO

IF INDEXPROPERTY(OBJECT_ID('ac.AC_DAILY'), 'IX_acd_date', 'IndexID') IS NULL
CREATE NONCLUSTERED INDEX IX_acd_date ON ac.AC_DAILY (D_DATE) INCLUDE (API_UWI, GROSS_OIL_BBL, GROSS_GAS_MCF);
GO

IF INDEXPROPERTY(OBJECT_ID('ac.AC_DAILY'), 'IX_acd_updated', 'IndexID') IS NULL
CREATE NONCLUSTERED INDEX IX_acd_updated ON ac.AC_DAILY (UPDATED_TS);
GO
