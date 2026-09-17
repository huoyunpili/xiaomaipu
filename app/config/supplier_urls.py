"""Only this URL configuration is reachable through the public upload gateway."""

from django.urls import include, path

urlpatterns = [path("supplier/", include("app.workbench.supplier_urls"))]
