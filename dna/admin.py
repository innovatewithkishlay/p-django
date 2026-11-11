from django.contrib import admin

from .models import AnalysisRun, FileMetric, Finding, Repository, Snapshot


@admin.register(Repository)
class RepositoryAdmin(admin.ModelAdmin):
    list_display = ("full_name", "source", "primary_language", "last_analyzed_at")
    search_fields = ("name", "owner", "url")


@admin.register(Snapshot)
class SnapshotAdmin(admin.ModelAdmin):
    list_display = ("repository", "label", "committed_at", "health_score", "code_lines", "security_issues")
    list_filter = ("repository",)


@admin.register(Finding)
class FindingAdmin(admin.ModelAdmin):
    list_display = ("title", "severity", "category", "snapshot", "is_regression")
    list_filter = ("severity", "category", "is_regression")
    search_fields = ("title", "file_path")


@admin.register(AnalysisRun)
class AnalysisRunAdmin(admin.ModelAdmin):
    list_display = ("repository", "status", "stage", "progress", "started_at", "finished_at")
    list_filter = ("status",)


@admin.register(FileMetric)
class FileMetricAdmin(admin.ModelAdmin):
    list_display = ("path", "snapshot", "language", "code_lines", "complexity", "churn", "hotspot_score")
    search_fields = ("path",)
