from django.core.exceptions import PermissionDenied


def require_admin(user):
    if not user.is_authenticated or not user.is_shop_admin:
        raise PermissionDenied("此操作需要管理员权限。")


def require_operator(user):
    if not user.is_authenticated or not user.is_active:
        raise PermissionDenied("请先登录后再操作。")
