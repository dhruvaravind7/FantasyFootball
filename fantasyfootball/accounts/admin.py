from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User

@admin.register(User)
class AccountAdmin(UserAdmin):
    list_display = ["email", "username", "is_staff"]
    search_fields = ["email", "username"]
    ordering = ["email"]
    fieldsets = [
        (None, {"fields": ["email", "username", "password"]}),
        ("Permissions", {"fields": ["is_active", "is_staff", "is_superuser"]}),
    ]
    
    add_fieldsets = [
        (None, {"fields": ["email", "username", "password1", "password2"]})
    ]