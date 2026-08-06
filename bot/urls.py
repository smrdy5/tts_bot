from django.urls import path
from . import views

urlpatterns = [
    path('telegram-webhook/', views.telegram_webhook, name='telegram_webhook'),
]
