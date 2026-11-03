from django.shortcuts import render
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from .serializers import RegisterSerializer, UserSerializer

# Create your views here.
class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]
    
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)     # request.data is the JSON the app sent
        serializer.is_valid(raise_exception=True)               # Runs the validate_ methods; sends 400 if any fail
        user = serializer.save()                                # Runs create
        refresh = RefreshToken.for_user(user)                   # Makes a token pair for this user
        
        return Response({"user": UserSerializer(user).data, "refresh": str(refresh), "access": str(refresh.access_token)}, status=status.HTTP_201_CREATED)

class LoginView(TokenObtainPairView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"
    
class MeView(APIView):
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        return Response(UserSerializer(request.user).data)