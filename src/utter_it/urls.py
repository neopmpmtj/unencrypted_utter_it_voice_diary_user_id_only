"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from src.recordings.views import upload_to_drive_page

urlpatterns = [
    path('admin/', admin.site.urls),
    path('i18n/', include('django.conf.urls.i18n')),
    path('src.accounts/', include('src.accounts.urls')),
    path('', include('src.core.urls')),
    path("ingestion/", include("src.ingestion.urls")),
    path('voice/', include('src.recordings.urls')),
    path('voice/', include('src.quotas.urls')),
    path('text-input/', include('src.text_input.urls')),
    path('upload/', upload_to_drive_page, name='upload_to_drive_page'),
    path('', include('src.entries.urls')),
    path('chat/', include('src.retrieval.urls')),
    path('', include('src.text_rewrite.urls')),
    path('batch-calendar/', include('src.batch_calendar.urls')),
    path('calendar/', include('src.batch_calendar.urls_webhook')),
    path('tools/', include([
        path('test-microphone/', include('src.vd_tools.test_microphone.urls')),
        path('', include('src.vd_tools.recent_recordings.urls')),
    ])),
    path('billing/', include('src.billing.urls')),
    path('api/gigo/', include('src.gigo.urls')),
    path('gmail-parsers/', include('src.gmail_parsers.urls')),
    path('invoice-parser/', include('src.invoice_parser.urls')),

    path('', include('src.managed_lists.urls')),
    path('', include('src.list_parser.urls')),
    path('', include('src.financial_parser.urls')),
]

# Serve media and static files in development
if settings.DEBUG:
    urlpatterns += [
        path("__reload__/", include("django_browser_reload.urls")),
    ]
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    # Use staticfiles finders so admin CSS/JS and app static files are served without collectstatic
    from django.contrib.staticfiles.urls import staticfiles_urlpatterns

    urlpatterns += staticfiles_urlpatterns()

