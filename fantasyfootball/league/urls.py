from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("teams", views.TeamViewSet)
router.register("games", views.GameViewSet) 
router.register("players", views.PlayerViewSet)

urlpatterns = router.urls