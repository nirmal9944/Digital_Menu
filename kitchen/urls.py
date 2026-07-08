from django.urls import path
from . import views

app_name = 'kitchen'

urlpatterns = [
    # Auth
    path('login/',   views.kds_login,  name='login'),
    path('logout/',  views.kds_logout, name='logout'),

    # KDS display + poll
    path('',                               views.kds_display,        name='kds'),
    path('poll/',                          views.kds_poll,           name='poll'),

    # AJAX actions
    path('item/<int:item_id>/status/',       views.update_item_status, name='item_status'),
    path('ticket/<int:ticket_id>/status/',   views.set_order_status,   name='order_status'),
    path('ticket/<int:ticket_id>/priority/', views.set_priority,       name='priority'),
    path('ticket/<int:ticket_id>/acknowledge/', views.acknowledge_ticket, name='acknowledge'),
    path('ticket/<int:ticket_id>/note/',     views.save_kitchen_note,  name='note'),
]