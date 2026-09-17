from django.urls import path

from . import supplier_views, views
from .importing import history

urlpatterns = [
    path("batch/<uuid:pk>/text/", supplier_views.download_text, name="wb-batch-text"),
    path("batch/<uuid:pk>/link/", supplier_views.create_link, name="wb-batch-link"),
    path("batch/<uuid:pk>/revoke/", supplier_views.revoke, name="wb-batch-revoke"),
    path("supplier-video/<uuid:pk>/", supplier_views.video_download, name="wb-supplier-video"),
    path("shipping/", views.listing, {"area": "shipping"}, name="wb-shipping"),
    path("pending/", views.listing, {"area": "pending"}, name="wb-pending"),
    path("refunds/", views.listing, {"area": "refunds"}, name="wb-refunds"),
    path("orders/", views.listing, name="wb-orders"),
    path("orders/<uuid:pk>/", views.detail, name="wb-detail"),
    path("orders/<uuid:pk>/recovery/", views.recovery, name="wb-recovery"),
    path("orders/<uuid:pk>/product-image/", views.product_image, name="wb-product-image"),
    path("costs/", views.costs, name="wb-costs"),
    path("profits/", views.profits, name="wb-profits"),
    path("export/<str:kind>/", views.export, name="wb-export"),
    path("batch/<uuid:pk>/", views.batch, name="wb-batch"),
    path("batch/<uuid:pk>/image/", views.batch_image, name="wb-batch-image"),
    path("image/<uuid:pk>/", views.image_download, name="wb-image"),
    path("settings/", views.settings, name="wb-settings"),
    path("history/", history, name="wb-history"),
]
