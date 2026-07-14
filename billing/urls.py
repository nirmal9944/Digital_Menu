from django.urls import path
from . import views

app_name = 'billing'

urlpatterns = [
    # Auth
    path('login/',  views.billing_login,  name='login'),
    path('logout/', views.billing_logout, name='logout'),

    # Dashboard + poll
    path('',      views.dashboard,      name='dashboard'),
    path('poll/', views.dashboard_poll, name='poll'),

    # AJAX actions
    path('bill/<int:bill_id>/status/', views.set_bill_status, name='set_status'),
    path('bill/<int:bill_id>/',        views.bill_detail,     name='bill_detail'),
    path('bill/<int:bill_id>/invoice/', views.invoice,        name='invoice'),
    path('table/<int:table_id>/create-bill/', views.create_bill, name='create_bill'),

    # Reports
    path('report/',      views.report,      name='report'),
    path('report/data/', views.report_data, name='report_data'),
]
