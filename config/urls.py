# config/urls.py
from django.contrib import admin
from django.urls import path, include
from orders.views import ( root_redirect, company_dashboard_view, 
                          dispatch_dashboard_view, motoboy_tasks_view, 
                          admin_dashboard_view, os_create_view, 
                          motoboy_update_status, assign_motoboy_view, report_problem_view, resolve_os_problem)
from accounts.views import register_user_view, custom_logout
from orders import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/logout/', custom_logout, name='logout'),
    path('os/<int:os_id>/cancelar/', views.cancel_os_view, name='cancel_os'),
    path('accounts/', include('django.contrib.auth.urls')),
    path('', root_redirect, name='root'),
    path('painel-admin/', admin_dashboard_view, name='admin_dashboard'),
    path('painel-empresa/', company_dashboard_view, name='company_dashboard'),
    path('painel-despacho/', dispatch_dashboard_view, name='dispatch_dashboard'),
    path('nova-os/', os_create_view, name='os_create'),
    path('minhas-entregas/', motoboy_tasks_view, name='motoboy_tasks'),
    path('minhas-entregas/atualizar/<int:stop_id>/', motoboy_update_status, name='motoboy_update_status'),
    path('painel-despacho/atribuir/<int:os_id>/', assign_motoboy_view, name='assign_motoboy'),
    path('painel-geral/cadastrar-usuario/', register_user_view, name='register_user'),
    path('motoboy/perfil/', views.motoboy_profile_view, name='motoboy_profile'),   
    path('motoboy/heartbeat/', views.motoboy_heartbeat_view, name='motoboy_heartbeat'),
    path('painel-despacho/reordenar-paradas/', views.reorder_stops_view, name='reorder_stops'),
    path('os/<int:os_id>/stops/', views.get_route_stops, name='get_route_stops'),
    path('os/mesclar/', views.merge_os_view, name='merge_os'),
    path('minhas-entregas/problema/<int:stop_id>/', report_problem_view, name='report_problem'),
    path('painel-despacho/resolver/<int:os_id>/', resolve_os_problem, name='resolve_os_problem'),
    path('painel-despacho/transferir/<int:os_id>/', views.transfer_route_view, name='transfer_route'),
    path('painel-despacho/devolver/<int:os_id>/', views.create_return_view, name='create_return'),
]