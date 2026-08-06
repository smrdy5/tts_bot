from django.db import models

class UserUsage(models.Model):
    user_id = models.BigIntegerField(unique=True)
    usage_count = models.IntegerField(default=0)
    last_reset_date = models.DateField(auto_now_add=True)
    selected_voice = models.CharField(max_length=10, default="male")
    custom_voice_b64 = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"User {self.user_id} - Voice: {self.selected_voice} - Count: {self.usage_count}"
