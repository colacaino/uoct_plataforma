from django.db.models import Q

from .models import Analysis, Cruce


def is_admin_user(user):
    return bool(user and user.is_authenticated and (user.is_superuser or getattr(user, "is_admin_role", False)))


def visible_cruces(user):
    queryset = Cruce.objects.all()
    if is_admin_user(user):
        return queryset
    return queryset.filter(Q(created_by=user) | Q(shared_with=user)).distinct()


def visible_analyses(user):
    queryset = Analysis.objects.all()
    if is_admin_user(user):
        return queryset
    return queryset.filter(
        Q(created_by=user)
        | Q(shared_with=user)
        | Q(cruce__created_by=user)
        | Q(cruce__shared_with=user)
    ).distinct()


def can_manage_cruce(user, cruce):
    if is_admin_user(user):
        return True
    return bool(cruce.created_by_id == user.id)


def can_manage_analysis(user, analysis):
    if is_admin_user(user):
        return True
    return bool(analysis.created_by_id == user.id or analysis.cruce.created_by_id == user.id)
