from django.contrib.auth import views as auth_views
from django.urls import include, path

from app.accounts.views import ThrottledLoginView, account_settings, first_owner_setup
from app.catalog import views as catalog_views
from app.common import views
from app.contacts import views as contact_views
from app.evidence import views as evidence_views
from app.importing import views as import_views
from app.insights import views as insight_views
from app.integrations import views as integration_views
from app.inventory import views as inventory_views
from app.operations import views as operation_views
from app.orders import views as order_views
from app.procurement import views as procurement_views
from app.shops.channel_views import channel_edit
from app.shops.views import settings_view
from app.workbench import views as workspace_views

urlpatterns = [
    path("supplier/", include("app.workbench.supplier_urls")),
    path("workspace/", include("app.workbench.urls")),
    path("health/live/", views.health_live, name="health-live"),
    path("health/ready/", views.health_ready, name="health-ready"),
    path("operations/", operation_views.bottleneck_list, name="bottleneck-list"),
    path(
        "operations/thresholds/", operation_views.threshold_settings, name="bottleneck-thresholds"
    ),
    path(
        "operations/followups/<uuid:followup_id>/",
        operation_views.followup_edit,
        name="followup-edit",
    ),
    path(
        "products/<uuid:sku_id>/listing-mappings/new/",
        operation_views.listing_mapping_new,
        name="listing-mapping-new",
    ),
    path(
        "purchases/<uuid:purchase_id>/supply-allocations/new/",
        operation_views.supply_allocate,
        name="supply-allocation-new",
    ),
    path("integrations/xgj/", integration_views.home, name="xgj-home"),
    path("integrations/xgj/push/", integration_views.webhook, name="xgj-webhook"),
    path("integrations/xgj/order/<uuid:row_id>/", integration_views.detail, name="xgj-detail"),
    path("integrations/xgj/<str:operation>/", integration_views.operate, name="xgj-operate"),
    path("risks/sources/<uuid:source_id>/", insight_views.source_new, name="intel-source-edit"),
    path("settings/channels/new/", channel_edit, name="channel-new"),
    path("settings/channels/<uuid:channel_id>/", channel_edit, name="channel-edit"),
    path("reports/", insight_views.reports, name="reports"),
    path("risks/", insight_views.risk_list, name="risk-list"),
    path(
        "risks/<uuid:sku_id>/price/",
        insight_views.market_record,
        {"kind": "price"},
        name="market-price",
    ),
    path(
        "risks/<uuid:sku_id>/event/",
        insight_views.market_record,
        {"kind": "event"},
        name="market-event",
    ),
    path("risks/sources/new/", insight_views.source_new, name="intel-source-new"),
    path("imports/", import_views.import_home, name="import-home"),
    path("imports/template/", import_views.import_template, name="import-template"),
    path("imports/<uuid:job_id>/", import_views.import_detail, name="import-detail"),
    path("imports/<uuid:job_id>/confirm/", import_views.import_confirm, name="import-confirm"),
    path("imports/<uuid:job_id>/errors/", import_views.import_errors, name="import-errors"),
    path("orders/<uuid:order_id>/evidence/", evidence_views.upload, name="evidence-upload"),
    path("orders/<uuid:order_id>/evidence/qr/", evidence_views.qr, name="evidence-qr"),
    path("evidence/<uuid:video_id>/", evidence_views.video, name="evidence-video"),
    path("customers/", contact_views.customer_list, name="customer-list"),
    path("customers/new/", contact_views.customer_edit, name="customer-new"),
    path("customers/<uuid:customer_id>/", contact_views.customer_detail, name="customer-detail"),
    path("customers/<uuid:customer_id>/edit/", contact_views.customer_edit, name="customer-edit"),
    path(
        "direct-purchases/<uuid:purchase_id>/dispatch/",
        procurement_views.direct_dispatch,
        name="direct-dispatch",
    ),
    path(
        "purchases/for-order/<uuid:order_item_id>/",
        procurement_views.purchase_new,
        name="purchase-for-order",
    ),
    path(
        "purchase-receipts/<uuid:receipt_id>/allocate/",
        procurement_views.purchase_allocate,
        name="purchase-allocate",
    ),
    path(
        "purchases/<uuid:purchase_id>/return/<uuid:receipt_id>/",
        procurement_views.purchase_operate,
        {"operation": "return"},
        name="purchase-return",
    ),
    path(
        "purchases/<uuid:purchase_id>/logistics/<str:operation>/",
        procurement_views.purchase_logistics,
        name="purchase-logistics",
    ),
    path("purchases/", procurement_views.purchase_list, name="purchase-list"),
    path("purchases/new/", procurement_views.purchase_new, name="purchase-new"),
    path(
        "purchases/from-quote/<uuid:quote_id>/",
        procurement_views.purchase_new,
        name="purchase-from-quote",
    ),
    path(
        "purchases/<uuid:purchase_id>/", procurement_views.purchase_detail, name="purchase-detail"
    ),
    path(
        "purchases/<uuid:purchase_id>/<str:operation>/",
        procurement_views.purchase_operate,
        name="purchase-operate",
    ),
    path("suppliers/", procurement_views.supplier_list, name="supplier-list"),
    path("suppliers/new/", procurement_views.supplier_form, name="supplier-new"),
    path(
        "suppliers/<uuid:supplier_id>/", procurement_views.supplier_detail, name="supplier-detail"
    ),
    path(
        "suppliers/<uuid:supplier_id>/edit/", procurement_views.supplier_form, name="supplier-edit"
    ),
    path("suppliers/<uuid:supplier_id>/quotes/new/", procurement_views.quote_new, name="quote-new"),
    path("products/", catalog_views.product_list, name="product-list"),
    path("products/new/", catalog_views.product_form, name="product-new"),
    path("products/<uuid:sku_id>/", catalog_views.product_detail, name="product-detail"),
    path("products/<uuid:sku_id>/edit/", catalog_views.product_form, name="product-edit"),
    path("products/<uuid:sku_id>/receive/", inventory_views.stock_receive, name="stock-receive"),
    path("inventory/<uuid:lot_id>/edit/", inventory_views.lot_edit, name="lot-edit"),
    path("inventory/<uuid:lot_id>/adjust/", inventory_views.stock_adjust, name="stock-adjust"),
    path("orders/", order_views.order_list, name="order-list"),
    path("orders/new/", catalog_views.product_list, {"for_order": True}, name="order-choose"),
    path("orders/new/<uuid:sku_id>/", order_views.order_new, name="order-new"),
    path("orders/<uuid:order_id>/", order_views.order_detail, name="order-detail"),
    path(
        "orders/<uuid:order_id>/actions/<str:operation>/",
        order_views.order_operate,
        name="order-operate",
    ),
    path(
        "orders/<uuid:order_id>/customer-payment/",
        order_views.customer_payment_record,
        name="customer-payment-record",
    ),
    path("orders/<uuid:order_id>/money/<str:kind>/", order_views.money_record, name="money-record"),
    path(
        "returns/receive/<uuid:reservation_id>/", order_views.return_receive, name="return-receive"
    ),
    path("returns/inspect/<uuid:return_id>/", order_views.return_inspect, name="return-inspect"),
    path("", workspace_views.dashboard, name="dashboard"),
    path(
        "login/",
        ThrottledLoginView.as_view(),
        name="login",
    ),
    path("setup/", first_owner_setup, name="first-owner-setup"),
    path("account/", account_settings, name="account-settings"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("settings/shop/", settings_view, name="shop-settings"),
    path("audit/", views.audit_list, name="audit-list"),
    path("health/", views.health, name="health"),
]
handler403 = "app.common.views.forbidden"
handler404 = "app.common.views.not_found"
handler500 = "app.common.views.server_error"
