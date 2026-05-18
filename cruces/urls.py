from django.urls import path

from . import views

app_name = "cruces"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("dashboard-rutas-html/", views.legacy_routes_dashboard_view, name="legacy_routes_dashboard"),
    path("cruces/", views.CruceListView.as_view(), name="list"),
    path("cruces/importar-bitacora/", views.bitacora_import_view, name="import_bitacora"),
    path("cruces/nuevo/", views.CruceCreateView.as_view(), name="create"),
    path("cruces/<int:pk>/", views.CruceDetailView.as_view(), name="detail"),
    path("cruces/<int:pk>/equipo/compartir/", views.cruce_share_user_view, name="cruce_share_user"),
    path("cruces/<int:pk>/equipo/<int:user_id>/quitar/", views.cruce_unshare_user_view, name="cruce_unshare_user"),
    path("cruces/<int:pk>/archivos/subir/", views.cruce_file_upload_view, name="cruce_file_upload"),
    path("cruces/<int:pk>/editar/", views.CruceUpdateView.as_view(), name="update"),
    path("cruces/<int:pk>/eliminar/", views.CruceDeleteView.as_view(), name="delete"),
    path("archivos/<int:pk>/descargar/", views.uploaded_file_download_view, name="uploaded_file_download"),
    path("archivos/<int:pk>/eliminar/", views.uploaded_file_delete_view, name="uploaded_file_delete"),
    path("analisis/", views.AnalysisListView.as_view(), name="analysis_list"),
    path("analisis/nuevo/", views.analysis_create_view, name="analysis_create"),
    path("analisis/<int:pk>/", views.AnalysisDetailView.as_view(), name="analysis_detail"),
    path("analisis/<int:pk>/excel/", views.analysis_export_excel_view, name="analysis_export_excel"),
    path("analisis/<int:pk>/pdf/", views.analysis_export_pdf_view, name="analysis_export_pdf"),
    path("analisis/<int:pk>/compartir/", views.analysis_share_create_view, name="analysis_share_create"),
    path("compartidos/<uuid:token>/", views.shared_report_view, name="shared_report"),
    path("compartidos/<int:pk>/revocar/", views.shared_report_revoke_view, name="shared_report_revoke"),
]
