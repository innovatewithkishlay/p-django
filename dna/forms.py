from django import forms
from django.conf import settings

from .analyzer.git import parse_repo_input


class RepositoryForm(forms.Form):
    repo = forms.CharField(
        max_length=500,
        widget=forms.TextInput(attrs={
            "placeholder": "github.com/owner/repository",
            "autocomplete": "off",
            "spellcheck": "false",
            "autofocus": True,
        }),
    )

    def clean_repo(self):
        value = self.cleaned_data["repo"]
        try:
            source, owner, name, url = parse_repo_input(value, allow_local=settings.DNA_ALLOW_LOCAL_PATHS)
        except ValueError as exc:
            raise forms.ValidationError(str(exc))
        self.parsed = {"source": source, "owner": owner, "name": name, "url": url}
        return value
