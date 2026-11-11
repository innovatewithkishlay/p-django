"""
Data model for Codebase DNA.

A ``Repository`` is analysed into a series of ``Snapshot`` rows, one per
sampled commit. Every snapshot owns the per-file measurements
(``FileMetric``) and the human-readable ``Finding`` rows that explain what
changed and what to do about it. ``AnalysisRun`` tracks a single run so the
UI can show progress while work happens in the background.
"""

from django.db import models
from django.urls import reverse
from django.utils.text import slugify


class Repository(models.Model):
    class Source(models.TextChoices):
        GITHUB = "github", "GitHub"
        GIT = "git", "Git URL"
        LOCAL = "local", "Local folder"

    name = models.CharField(max_length=200)
    owner = models.CharField(max_length=200, blank=True)
    slug = models.SlugField(max_length=220, unique=True)
    url = models.CharField(max_length=500, unique=True)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.GITHUB)
    default_branch = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    primary_language = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_analyzed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_analyzed_at", "-created_at"]
        verbose_name_plural = "repositories"

    def __str__(self):
        return self.full_name

    @property
    def full_name(self):
        return f"{self.owner}/{self.name}" if self.owner else self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.full_name.replace("/", "-")) or "repo"
            slug, n = base, 2
            while Repository.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("dna:repo", args=[self.slug])

    @property
    def latest_snapshot(self):
        return self.snapshots.order_by("-committed_at").first()

    @property
    def active_run(self):
        return self.runs.filter(status__in=[AnalysisRun.Status.QUEUED, AnalysisRun.Status.RUNNING]).first()

    @property
    def web_url(self):
        if self.source == self.Source.GITHUB:
            return f"https://github.com/{self.owner}/{self.name}"
        return ""


class AnalysisRun(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    repository = models.ForeignKey(Repository, on_delete=models.CASCADE, related_name="runs")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    stage = models.CharField(max_length=200, blank=True)
    progress = models.PositiveSmallIntegerField(default=0)
    error = models.TextField(blank=True)
    snapshots_created = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.repository} · {self.status}"

    @property
    def is_active(self):
        return self.status in {self.Status.QUEUED, self.Status.RUNNING}


class Snapshot(models.Model):
    """The state of a repository at one commit."""

    repository = models.ForeignKey(Repository, on_delete=models.CASCADE, related_name="snapshots")
    commit_sha = models.CharField(max_length=40)
    commit_message = models.CharField(max_length=500, blank=True)
    author = models.CharField(max_length=200, blank=True)
    committed_at = models.DateTimeField()
    tag = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Size
    total_files = models.PositiveIntegerField(default=0)
    code_lines = models.PositiveIntegerField(default=0)
    comment_lines = models.PositiveIntegerField(default=0)
    blank_lines = models.PositiveIntegerField(default=0)
    function_count = models.PositiveIntegerField(default=0)
    languages = models.JSONField(default=dict)  # {"Python": 1234, ...} code lines

    # Complexity & structure
    avg_complexity = models.FloatField(default=0)
    max_complexity = models.PositiveIntegerField(default=0)
    complex_functions = models.PositiveIntegerField(default=0)  # CC > 10
    long_functions = models.PositiveIntegerField(default=0)  # > 60 lines
    large_files = models.PositiveIntegerField(default=0)  # > 500 code lines
    avg_maintainability = models.FloatField(default=0)
    duplication_ratio = models.FloatField(default=0)  # 0..1
    max_nesting = models.PositiveIntegerField(default=0)

    # Testing
    test_files = models.PositiveIntegerField(default=0)
    test_lines = models.PositiveIntegerField(default=0)
    test_ratio = models.FloatField(default=0)  # test lines / source lines

    # Dependencies
    dependency_count = models.PositiveIntegerField(default=0)
    unpinned_dependencies = models.PositiveIntegerField(default=0)
    dependencies = models.JSONField(default=list)

    # Security & hygiene
    security_issues = models.PositiveIntegerField(default=0)
    todo_count = models.PositiveIntegerField(default=0)
    has_readme = models.BooleanField(default=False)
    has_ci = models.BooleanField(default=False)
    has_license = models.BooleanField(default=False)
    comment_ratio = models.FloatField(default=0)

    # Scores (0-100, higher is healthier)
    score_complexity = models.PositiveSmallIntegerField(default=0)
    score_maintainability = models.PositiveSmallIntegerField(default=0)
    score_testing = models.PositiveSmallIntegerField(default=0)
    score_security = models.PositiveSmallIntegerField(default=0)
    score_dependencies = models.PositiveSmallIntegerField(default=0)
    score_documentation = models.PositiveSmallIntegerField(default=0)
    health_score = models.PositiveSmallIntegerField(default=0)

    # Structure: top-level folders -> code lines, used for architecture view
    modules = models.JSONField(default=dict)
    # The most complex functions at this point: [{name, path, line, complexity, length}]
    hot_functions = models.JSONField(default=list)
    # A short plain-English paragraph describing this snapshot.
    summary = models.TextField(blank=True)

    class Meta:
        ordering = ["committed_at"]
        unique_together = [("repository", "commit_sha")]

    def __str__(self):
        return f"{self.repository} @ {self.short_sha}"

    @property
    def short_sha(self):
        return self.commit_sha[:7]

    @property
    def label(self):
        return self.tag or self.short_sha

    @property
    def grade(self):
        return grade_for(self.health_score)

    def get_absolute_url(self):
        return reverse("dna:snapshot", args=[self.repository.slug, self.short_sha])

    SCORE_FIELDS = [
        ("score_complexity", "Complexity"),
        ("score_maintainability", "Maintainability"),
        ("score_testing", "Testing"),
        ("score_security", "Security"),
        ("score_dependencies", "Dependencies"),
        ("score_documentation", "Documentation"),
    ]

    def scores(self):
        return [(label, getattr(self, field), field) for field, label in self.SCORE_FIELDS]


class FileMetric(models.Model):
    snapshot = models.ForeignKey(Snapshot, on_delete=models.CASCADE, related_name="files")
    path = models.CharField(max_length=500)
    language = models.CharField(max_length=50)
    code_lines = models.PositiveIntegerField(default=0)
    comment_lines = models.PositiveIntegerField(default=0)
    function_count = models.PositiveIntegerField(default=0)
    complexity = models.PositiveIntegerField(default=0)  # sum of function CC
    max_function_complexity = models.PositiveIntegerField(default=0)
    max_function_name = models.CharField(max_length=200, blank=True)
    maintainability = models.FloatField(default=100)
    max_nesting = models.PositiveSmallIntegerField(default=0)
    churn = models.PositiveIntegerField(default=0)  # commits touching file in window
    authors = models.PositiveSmallIntegerField(default=0)
    hotspot_score = models.FloatField(default=0)
    is_test = models.BooleanField(default=False)
    security_issues = models.PositiveSmallIntegerField(default=0)
    todo_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-hotspot_score"]
        indexes = [models.Index(fields=["snapshot", "path"])]

    def __str__(self):
        return self.path


class Finding(models.Model):
    class Severity(models.TextChoices):
        CRITICAL = "critical", "Critical"
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"
        GOOD = "good", "Good news"

    class Category(models.TextChoices):
        COMPLEXITY = "complexity", "Complexity"
        MAINTAINABILITY = "maintainability", "Maintainability"
        TESTING = "testing", "Testing"
        SECURITY = "security", "Security"
        DEPENDENCIES = "dependencies", "Dependencies"
        DOCUMENTATION = "documentation", "Documentation"
        ARCHITECTURE = "architecture", "Architecture"
        HOTSPOT = "hotspot", "Hotspot"

    SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "good": 4}

    snapshot = models.ForeignKey(Snapshot, on_delete=models.CASCADE, related_name="findings")
    category = models.CharField(max_length=20, choices=Category.choices)
    severity = models.CharField(max_length=10, choices=Severity.choices)
    title = models.CharField(max_length=250)
    explanation = models.TextField()
    recommendation = models.TextField(blank=True)
    file_path = models.CharField(max_length=500, blank=True)
    line = models.PositiveIntegerField(null=True, blank=True)
    priority = models.PositiveSmallIntegerField(default=50)  # lower = do first
    is_regression = models.BooleanField(default=False)

    class Meta:
        ordering = ["priority", "id"]

    def __str__(self):
        return self.title


def grade_for(score):
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"
    return "E"
