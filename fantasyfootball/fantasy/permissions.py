from rest_framework import permissions


class IsCommissionerOrReadOnly(permissions.BasePermission):
    """Members can read a league, but only its commissioner can change it."""

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        if obj.commissioner == request.user:
            return True