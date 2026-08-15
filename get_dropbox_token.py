# -*- coding: utf-8 -*-
"""
Одноразове отримання постійного refresh-токена Dropbox.

Запуск:  venv\\Scripts\\python.exe get_dropbox_token.py

Потрібні App key і App secret з https://www.dropbox.com/developers/apps
(вкладка Settings). У вкладці Permissions мають бути ввімкнені
files.content.write і files.content.read, після чого натисніть Submit.
"""

import sys
import urllib.parse
import urllib.request


def main() -> int:
    app_key = input("App key: ").strip()
    app_secret = input("App secret: ").strip()
    if not app_key or not app_secret:
        print("Потрібні і App key, і App secret.")
        return 1

    auth_url = (
        "https://www.dropbox.com/oauth2/authorize?"
        + urllib.parse.urlencode(
            {
                "client_id": app_key,
                "response_type": "code",
                "token_access_type": "offline",
            }
        )
    )
    print("\n1. Відкрийте це посилання у браузері:\n")
    print(auth_url)
    print("\n2. Дозвольте доступ і скопіюйте код, який покаже Dropbox.\n")
    code = input("Код авторизації: ").strip()
    if not code:
        print("Код не введено.")
        return 1

    data = urllib.parse.urlencode(
        {
            "code": code,
            "grant_type": "authorization_code",
            "client_id": app_key,
            "client_secret": app_secret,
        }
    ).encode()

    try:
        with urllib.request.urlopen(
            "https://api.dropboxapi.com/oauth2/token", data=data
        ) as resp:
            import json

            payload = json.load(resp)
    except Exception as e:
        print(f"Помилка обміну коду: {e}")
        return 1

    refresh_token = payload.get("refresh_token", "")
    if not refresh_token:
        print(f"Dropbox не повернув refresh_token: {payload}")
        return 1

    print("\nГотово. Впишіть у .env (і приберіть старий DROPBOX_ACCESS_TOKEN):\n")
    print(f"DROPBOX_APP_KEY={app_key}")
    print(f"DROPBOX_APP_SECRET={app_secret}")
    print(f"DROPBOX_REFRESH_TOKEN={refresh_token}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
