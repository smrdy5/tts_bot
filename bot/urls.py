from django.urls import path
from . import views

urlpatterns = [
    path('telegram-webhook/', views.telegram_webhook, name='telegram_webhook'),
    path('update-tunnel/', views.update_tunnel, name='update_tunnel'),
]
