from django.urls import path
from django.http import HttpResponse
from bot.views import telegram_webhook

def home_view(request):
    return HttpResponse("VoxCPM Telegram Voice Bot Backend is Running! 🎙️")

urlpatterns = [
    path('', home_view, name='home'),
    path('telegram-webhook/', telegram_webhook, name='webhook'),
]
