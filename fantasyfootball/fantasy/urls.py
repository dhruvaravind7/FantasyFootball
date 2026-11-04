from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("leagues", views.LeagueViewSet, basename="league")
router.register("teams", views.FantasyTeamViewSet, basename="team")

urlpatterns = router.urls