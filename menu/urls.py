from django.urls import path
from . import views

urlpatterns = [
    path('<int:table_number>/', views.landing, name='landing'),
    path('<int:table_number>/menu/', views.menu_page, name='menu'),

    # Order tracking page (what the customer sees on their phone)
    path(
        '<int:table_number>/order-tracking/',
        views.order_tracking,
        name='order_tracking',
    ),

    # JSON polled by order_tracking.html every few seconds to stay live
    path(
        '<int:table_number>/order-tracking/status/',
        views.order_status_api,
        name='order_status_api',
    ),

    # Cart page (renders the shell; actual cart lives in localStorage)
    path('<int:table_number>/cart/', views.cart_page, name='cart'),

    # Where the cart gets POSTed to become real Order + OrderItem rows
    path(
        '<int:table_number>/place-order/',
        views.place_order,
        name='place_order',
    ),

    # Customer taps "Request Bill" on order_tracking.html -> creates the Bill
    path(
        '<int:table_number>/request-bill/',
        views.request_bill,
        name='request_bill',
    ),

    # Quick-order button on menu.html (water, tissue, pickle, cold drink...)
    path(
        '<int:table_number>/quick-order/',
        views.quick_order,
        name='quick_order',
    ),
]