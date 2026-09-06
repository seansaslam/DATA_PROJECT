/* ============================================================================
   WELL_MASTER_DB  --  schema [wm]
   The corporate well header of record for CCT Oil & Cattle.
   One row per well, keyed by the 14-digit API / UWI.

   No foreign keys anywhere in this build -- systems are linked by primary key
   value only (API_UWI here, MERRICK_ID in ProCount, PROPNUM in Aries), exactly
   the way independent vendor systems link in the real world.
   ============================================================================ */

IF SCHEMA_ID('wm') IS NULL EXEC('CREATE SCHEMA wm');
GO

IF OBJECT_ID('wm.cct_well_master') IS NULL
CREATE TABLE wm.cct_well_master (
    API_UWI                 varchar(14)     NOT NULL,   -- 42-XXX-XXXXX-XX-XX, Texas state code 42
    WELL_NAME               varchar(100)    NOT NULL,
    WELL_NUMBER             varchar(20)     NOT NULL,
    OPERATOR_NAME           varchar(80)     NOT NULL,
    OPERATED_FLAG           bit             NOT NULL,   -- 1 = CCT operated, 0 = outside-operated WI
    AREA                    varchar(40)     NOT NULL,
    PAD_NAME                varchar(40)     NULL,
    CENTRAL_FACILITY        varchar(40)     NULL,
    FIELD_NAME              varchar(60)     NULL,
    COUNTY                  varchar(40)     NOT NULL,
    STATE_CODE              char(2)         NOT NULL,
    BASIN                   varchar(40)     NOT NULL,
    RESERVOIR               varchar(40)     NULL,
    WELL_STATUS             varchar(20)     NOT NULL,   -- PRODUCING | SHUT-IN | TA | DUC
    WELL_TYPE               varchar(20)     NOT NULL,   -- OIL | GAS
    SURFACE_LATITUDE        decimal(9,6)    NULL,
    SURFACE_LONGITUDE       decimal(9,6)    NULL,
    BOTTOMHOLE_LATITUDE     decimal(9,6)    NULL,
    BOTTOMHOLE_LONGITUDE    decimal(9,6)    NULL,
    SPUD_DATE               date            NULL,
    COMPLETION_DATE         date            NULL,
    FIRST_PROD_DATE         date            NULL,
    LATERAL_LENGTH_FT       int             NULL,
    TOTAL_DEPTH_FT          int             NULL,
    WORKING_INTEREST        decimal(9,6)    NOT NULL,
    NET_REVENUE_INTEREST    decimal(9,6)    NOT NULL,
    CREATED_TS              datetime2(3)    NOT NULL CONSTRAINT DF_wm_created DEFAULT SYSUTCDATETIME(),
    UPDATED_TS              datetime2(3)    NOT NULL CONSTRAINT DF_wm_updated DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_cct_well_master PRIMARY KEY CLUSTERED (API_UWI)
);
GO

IF INDEXPROPERTY(OBJECT_ID('wm.cct_well_master'), 'IX_wm_operated', 'IndexID') IS NULL
CREATE NONCLUSTERED INDEX IX_wm_operated ON wm.cct_well_master (OPERATED_FLAG, AREA) INCLUDE (WELL_NAME, WELL_STATUS);
GO

IF INDEXPROPERTY(OBJECT_ID('wm.cct_well_master'), 'IX_wm_updated', 'IndexID') IS NULL
CREATE NONCLUSTERED INDEX IX_wm_updated ON wm.cct_well_master (UPDATED_TS);
GO
