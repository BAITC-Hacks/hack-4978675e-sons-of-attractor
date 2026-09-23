# Каталог

Поместите исходный CSV без изменений в `catalog.csv` либо задайте `DATASET_PATH`.
Ожидаемый исходник: `tz/hackathon dataset anonymized .csv`, 66 профилей.
SHA-256 из ТЗ: `6a724b6b7dfb5973343e68ba18dadb60fc807d87e3d78f03ee86fb26cb089f7d`.

В `catalog.csv` поставляется исходный файл; хеш и контрольные количества
проверяются тестом B1. Тестовые профили используются только во временных файлах.

Обязательные столбцы: `id`, `anon_name`, `city`, `categories`, `event_formats`,
`languages`, `busy_dates`, `price_from_kzt`, `max_hours`, `description`,
`synthetic`, `price_imputed`, `city_imputed`. Дополнительные столбцы допускаются.
UTF-8 с необязательным BOM; списки разделены `|`, флаги — `True`/`False`.
