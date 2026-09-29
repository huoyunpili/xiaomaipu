from django import forms
from django.core.validators import RegexValidator


class ApiCredentialsForm(forms.Form):
    app_key = forms.CharField(
        label="AppKey（应用 ID）",
        required=False,
        max_length=200,
        validators=[RegexValidator(r"\A[^\s\x00-\x1f\x7f]+\Z", "AppKey 不能包含空格或换行。")],
        widget=forms.TextInput(attrs={"autocomplete": "off", "spellcheck": "false"}),
        help_text="填写闲管家提供的 AppKey；已有配置时可留空沿用。",
    )
    app_secret = forms.CharField(
        label="AppSecret（应用密钥）",
        required=False,
        max_length=500,
        validators=[RegexValidator(r"\A[^\s\x00-\x1f\x7f]+\Z", "AppSecret 不能包含空格或换行。")],
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}, render_value=False),
        help_text="已保存的密钥不会显示。留空沿用；更换 AppKey 时需同时填写对应密钥。",
    )
