# МедХар — медична характеристика (Excel only)

Мінімалізований застосунок для генерації **тільки** медичної характеристики з Excel-даних.

## Принцип роботи

- без SQLite/БД;
- джерело даних: файли `.xlsx` у папці `data/`;
- генерація документа: `medical_characteristic_template.docx`.

## Необхідні файли

У `data/`:

- `treatments_2024.xlsx` та `treatments_2025.xlsx` — **обов’язкові**, об’єднуються в одну базу 2024–2025
- `treatments_2026.xlsx` — **опційно**, поточний рік (файл можна оновлювати; зміна на диску скидає кеш)
- `treatments_final.xlsx` — опційний архів (якщо є, додається першим; при однаковому ПІБ+даті пріоритет у новіших файлів)

## Запуск

```bash
pip install -r requirements.txt
python app.py
```

Відкрити: [http://127.0.0.1:5000/medical-characteristic](http://127.0.0.1:5000/medical-characteristic)

## Маршрути

- `GET /` -> редірект на `/medical-characteristic`
- `GET|POST /medical-characteristic`
- `GET /api/search_pib?q=...`
- `GET /api/stats`

## Важливо

Якщо файл має назву `treatments_2026.xlsx.xlsx`, перейменуйте його на `treatments_2026.xlsx`.
