from django.db import models
from django.contrib.auth.models import AbstractUser, UserManager

# Create your models here.
class AccountManager(UserManager):
    def get_by_natural_key(self, email):
        return self.get(email__iexact=email)

class User(AbstractUser):
    email = models.EmailField(unique=True)
    
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]
    
    objects = AccountManager()