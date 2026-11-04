from rest_framework import permissions


class IsCommissionerOrReadOnly(permissions.BasePermission):
    """Members can read a league, but only its commissioner can change it."""

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return obj.commissioner == request.user

class IsOwnerOrCommissioner(permissions.BasePermission):
    """Members can see a team, but only its owner or the league's commissioner can change it."""

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        
        if obj.league.commissioner == request.user or obj.owner == request.user:
            return True
        return False