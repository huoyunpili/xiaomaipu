from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import UserCreationForm

from .models import User


class FirstOwnerForm(UserCreationForm):
    class Meta:
        model = User
        fields = ("username", "display_name")
        labels = {
            "username": "用户名",
            "display_name": "你的称呼（选填）",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].help_text = "以后用这个用户名登录，可以使用中文、字母或数字。"
        self.fields["password1"].label = "设置密码"
        self.fields["password2"].label = "再输入一次密码"


class AccountSettingsForm(forms.ModelForm):
    current_password = forms.CharField(
        label="当前密码（修改用户名或密码时填写）",
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
    password1 = forms.CharField(
        label="新密码（不修改请留空）",
        required=False,
        strip=False,
        widget=forms.PasswordInput,
    )
    password2 = forms.CharField(
        label="再输入一次新密码",
        required=False,
        strip=False,
        widget=forms.PasswordInput,
    )

    class Meta:
        model = User
        fields = ("username", "display_name")
        labels = {"username": "用户名", "display_name": "你的称呼（选填）"}

    def clean(self):
        cleaned = super().clean() or {}
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if (
            password1
            or password2
            or cleaned.get("username", self.instance.username) != self.instance.username
        ):
            if not self.instance.check_password(cleaned.get("current_password", "")):
                self.add_error("current_password", "请输入正确的当前密码。")
        if password1 or password2:
            if password1 != password2:
                self.add_error("password2", "两次输入的密码不一致。")
            elif password1:
                password_validation.validate_password(password1, self.instance)
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password1")
        if password:
            user.set_password(password)
        if commit:
            user.save()
        return user
