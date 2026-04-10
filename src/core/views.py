from django.shortcuts import render
from django.views.generic import TemplateView

from src.accounts.decorators import app_admin_required


def home(request):
    return render(request, 'core/home.html')


class TermsView(TemplateView):
    """Terms of Service, Privacy Policy, and Data Retention."""

    template_name = 'legal/terms.html'

    def get_context_data(self, **kwargs):
        from src.accounts.deletion_config import get_deletion_retention_days

        ctx = super().get_context_data(**kwargs)
        ctx['retention_days'] = get_deletion_retention_days()
        return ctx


@app_admin_required
def test_page(request):
    return render(request, 'core/test.html')
