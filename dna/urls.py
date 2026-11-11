from django.urls import path

from . import views

app_name = "dna"

urlpatterns = [
    path("", views.home, name="home"),
    path("how-it-works/", views.how_it_works, name="how"),
    path("r/<slug:slug>/", views.repo_overview, name="repo"),
    path("r/<slug:slug>/timeline/", views.repo_timeline, name="timeline"),
    path("r/<slug:slug>/hotspots/", views.repo_hotspots, name="hotspots"),
    path("r/<slug:slug>/findings/", views.repo_findings, name="findings"),
    path("r/<slug:slug>/dependencies/", views.repo_dependencies, name="dependencies"),
    path("r/<slug:slug>/compare/", views.repo_compare, name="compare"),
    path("r/<slug:slug>/s/<str:sha>/", views.snapshot_detail, name="snapshot"),
    path("r/<slug:slug>/analyze/", views.reanalyze, name="reanalyze"),
    path("r/<slug:slug>/delete/", views.delete_repo, name="delete"),
    path("r/<slug:slug>/status.json", views.run_status, name="status"),
    path("api/r/<slug:slug>.json", views.api_repo, name="api_repo"),
]
