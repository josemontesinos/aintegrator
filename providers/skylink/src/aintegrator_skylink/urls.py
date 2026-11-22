"""URL routes for the simulated SkyLink provider."""

from __future__ import annotations

from django.contrib import admin
from django.urls import path, re_path

from .api import (
    AdvanceControlView,
    AvailabilityRequestCreateView,
    AvailabilityRequestDetailView,
    FaultsControlView,
    OrderDetailView,
    OrderListCreateView,
    ResetControlView,
    StateControlView,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    re_path(
        r"^api/v2/availability-requests/?$",
        AvailabilityRequestCreateView.as_view(),
        name="availability-create",
    ),
    re_path(
        r"^api/v2/availability-requests/(?P<request_id>[^/]+)/?$",
        AvailabilityRequestDetailView.as_view(),
        name="availability-detail",
    ),
    re_path(r"^api/v2/orders/?$", OrderListCreateView.as_view(), name="orders"),
    re_path(
        r"^api/v2/orders/(?P<order_id>[^/]+)/?$", OrderDetailView.as_view(), name="order-detail"
    ),
    path("_control/faults", FaultsControlView.as_view(), name="control-faults"),
    path("_control/reset", ResetControlView.as_view(), name="control-reset"),
    path("_control/advance", AdvanceControlView.as_view(), name="control-advance"),
    path("_control/state", StateControlView.as_view(), name="control-state"),
]
