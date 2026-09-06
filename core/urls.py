from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("system/<str:key>/", views.system_detail, name="system"),
    path("etl/", views.etl_console, name="etl"),
    path("connections/", views.connections_page, name="connections"),
    path("docs/", views.docs, name="docs"),

    # JSON
    path("api/overview", views.api_overview, name="api_overview"),
    path("api/system/<str:key>/status", views.api_system_status, name="api_system_status"),
    path("api/records/<str:key>/<str:table>", views.api_records, name="api_records"),
    path("api/test/<str:key>", views.api_test_connection, name="api_test"),
    path("api/etl/status", views.api_etl_status, name="api_etl_status"),
    path("api/job/<str:job_id>", views.api_job, name="api_job"),

    # actions
    path("api/reset", views.api_reset, name="api_reset"),
    path("api/churn", views.api_churn, name="api_churn"),
    path("api/mode", views.api_mode, name="api_mode"),
    path("api/etl/run", views.api_run_etl, name="api_run_etl"),
    path("api/ticker", views.api_ticker, name="api_ticker"),
]
