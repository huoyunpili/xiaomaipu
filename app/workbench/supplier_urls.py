from django.urls import path

from . import supplier_views as views

urlpatterns = [
    path("health/", views.gateway_health, name="supplier-health"),
    path("assets/<str:name>", views.asset, name="supplier-asset"),
    path("<str:token>/", views.portal, name="supplier-portal"),
    path("<str:token>/<uuid:pk>/video/", views.upload, name="supplier-upload"),
    path("<str:token>/<uuid:pk>/ship/", views.shipping, name="supplier-shipping"),
    path("<str:token>/<uuid:pk>/status/", views.status, name="supplier-status"),
    path("<str:token>/<uuid:pk>/image/", views.product_image, name="supplier-image"),
]
