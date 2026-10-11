"""API-only URL configuration for isolated backend tests."""

from django.urls import include, path

urlpatterns = [path("toto/", include("mysite.apps.todo_together.urls"))]
