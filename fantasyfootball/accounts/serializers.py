from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import User

class UserSerializer(serializers.ModelSerializer):
    """What the app sees about a user. Used by register and me. Never includes the password."""
    class Meta:
        model = User
        fields = ["id", "email", "username"]
        
class RegisterSerializer(serializers.ModelSerializer):
    """Checks a sign-up form and creates the account. Used by RegisterView."""
    password = serializers.CharField(write_only=True)
    
    class Meta:
        model = User
        fields = ["id", "email", "username", "password"]
    
    def validate_email(self, value):
        value = value.lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate(self, attrs: dict) -> dict:
        """Run Django's password rules with the new user's email and username, so "too similar" can be checked."""
        try:
            validate_password(attrs["password"], user=User(email=attrs.get("email", ""), username=attrs.get("username", "")))
        except DjangoValidationError as error:
            raise serializers.ValidationError({"password": list(error.messages)})
        return attrs
    
    def create(self, validated_data):
        return User.objects.create_user(**validated_data)
    