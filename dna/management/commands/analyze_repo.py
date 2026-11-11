from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from dna.analyzer.git import parse_repo_input
from dna.analyzer.pipeline import start_analysis
from dna.models import Repository


class Command(BaseCommand):
    help = "Analyse a repository from the command line, e.g. `manage.py analyze_repo pallets/flask`."

    def add_arguments(self, parser):
        parser.add_argument("repo", help="GitHub URL, owner/name, or a local git folder")
        parser.add_argument("--snapshots", type=int, default=settings.DNA_MAX_SNAPSHOTS,
                            help="How many points in history to measure (default: %(default)s)")
        parser.add_argument("--rebuild", action="store_true", help="Throw away existing snapshots first")

    def handle(self, *args, **opts):
        try:
            source, owner, name, url = parse_repo_input(opts["repo"], allow_local=True)
        except ValueError as exc:
            raise CommandError(str(exc))
        repo, created = Repository.objects.get_or_create(
            url=url, defaults={"source": source, "owner": owner, "name": name}
        )
        self.stdout.write(f"{'Adding' if created else 'Updating'} {repo.full_name}…")
        run = start_analysis(repo, reanalyze=opts["rebuild"], max_snapshots=opts["snapshots"], run_async=False)
        run.refresh_from_db()
        if run.status == run.Status.FAILED:
            raise CommandError(run.error)
        latest = repo.latest_snapshot
        self.stdout.write(self.style.SUCCESS(
            f"Done: {repo.snapshots.count()} snapshots. Latest health {latest.health_score}/100 (grade {latest.grade})."
        ))
        for finding in latest.findings.exclude(severity="good")[:5]:
            self.stdout.write(f"  [{finding.severity}] {finding.title}")
