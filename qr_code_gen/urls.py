from django.urls import path
from . import views

app_name = 'qr_code_gen'

urlpatterns = [
    path('', views.qr_generator_view, name='qr_generator'),
    path('generate-qr/', views.generate_qr, name='generate_qr'),
]