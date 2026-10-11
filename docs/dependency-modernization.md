# Dependency modernization

Этапы 1 и 2 завершены. Этап 1 внедрил инструменты разработки; этап 2 сократил
преобразования в двух production-адаптерах. Production-state, доставка, recovery,
подтверждения и расписания не менялись. Проверено по актуальным первичным источникам
11.10.2026. Этапы 3–4 ниже остаются планом, не выполненными миграциями.

## Использование

```sh
.venv/bin/python -m pip install -e '.[dev,diagnostics]'
make check
make diagnostics
```

`make check` запускает Ruff, mypy, Import Linter, unittest (включая Hypothesis и
offline HTTP replay) и Vulture. Нарушение архитектурного контракта блокирует проверку.
Находки Vulture выводятся как кандидаты для ручной проверки, не как ошибки качества;
ошибка самого инструмента не скрывается. `make diagnostics` дополнительно считает
cognitive complexity. Нет порога сложности, оценки продуктивности или автоудаления.
Все команды используют `.venv`; тесты не зависят от пакетов системного Python.

## Зависимости и сопровождение

| Библиотека | Версия / Python | Группа | Лицензия, обязательные зависимости | Сопровождение / применение |
|---|---|---|---|---|
| [Vulture](https://pypi.org/project/vulture/2.16/) | 2.16 / >=3.9 | dev | MIT; tomli только Python <3.11 | Релиз 25.03.2026; поиск неиспользуемых функций/классов и недостижимого кода |
| [Import Linter](https://pypi.org/project/import-linter/2.15/) | 2.15 / >=3.10 | dev | BSD-2-Clause; click, grimp, rich, typing-extensions, условный tomli | Релиз 04.09.2026; фактические зависимости и циклы |
| [Hypothesis](https://pypi.org/project/hypothesis/6.168.5/) | 6.168.5 / >=3.10 | dev | MPL-2.0; sortedcontainers, exceptiongroup на Python <3.11 | Релиз 05.10.2026; даты, интервалы, сохранение checkbox state |
| [dirty-equals](https://pypi.org/project/dirty-equals/0.11/) | 0.11 / >=3.9 | dev | MIT; без обязательных зависимостей | Релиз 17.11.2025; структурные assertions HTTP-кассет |
| [VCR.py](https://pypi.org/project/vcrpy/8.3.0/) | 8.3.0 / >=3.10 | dev | MIT; PyYAML, wrapt | Релиз 04.07.2026; replay синтетического Notion HTTP-контракта |
| [cognitive_complexity](https://pypi.org/project/cognitive-complexity/1.3.0/) | 1.3.0 / >=3.6 | diagnostics | MIT; setuptools в метаданных собранного wheel | Последний опубликованный релиз 09.08.2022; изолированная read-only диагностика |

Версии новых инструментов фиксированы, чтобы результат первого внедрения был
воспроизводимым. Транзитивные зависимости разрешает pip; это не полный lockfile.
Hypothesis 6.169 требует Python >=3.11: минимальная версия проекта не повышается.
Дополнительные extras этих библиотек (pytest, aiohttp, UI, pydantic) не включены.
Grimp содержит нативный компонент, VCR добавляет патчинг HTTP на время теста.
Проверка `pip check` дополняет тесты совместимости.
На этапе 2 полный набор из 348 тестов выполнен на Python 3.10.20 и 3.12.3.
На Python 3.12 также выполнен `make check` с архитектурными проверками и mypy.

Старый `cognitive_complexity` не используется в production или обязательном gate:
метрика приблизительна, вложенные функции и новые конструкции AST могут влиять на
результат. На текущем исходном коде инструмент выполняется; число — повод изучить
функцию, а не указание механически разделить её. Риск сопровождения ограничен
отдельным optional extra и командой, которую можно удалить независимо от приложения.

## Что заменено и что оставлено

Import Linter заменяет два собственных теста в `tests/architecture/test_imports.py`:
направление импортов integrations/platform и ручной DFS для циклов. Контракты
хранятся в `pyproject.toml`. `acyclic_siblings` проверяет capabilities, integrations,
platform и entrypoints, включая их потомков. CLI импортирует bootstrap, который
собирает worker/Telegram: этот корректный composition shape не объявляется циклом
между агрегированными корневыми пакетами. Отдельно запрещён bootstrap → CLI.
Контракты проверены внесением запрещённых импортов в изолированные копии `src`.

Тесты pure-stdlib для business code и публичных межмодульных API остаются:
их проектные правила не дублируются в Import Linter. Vulture не повторяет Ruff
проверками неиспользуемых imports/locals. Его кандидаты не являются доказательством
мёртвого кода: динамическое использование и публичные API требуют проверки callers.
Никакие production-функции не удаляются автоматически.

Hypothesis дополняет примеры свойствами полных календарных периодов, непересечения
предложенных слотов с занятостью и буфером, сохранения boolean state при перезапуске
и повторной записи. Tests используют временные файлы и подставные порты.

HTTP-кассеты содержат только синтетические данные. Обычные тесты работают в режиме
`none`: они не записывают реальные ответы и не требуют токенов. Проверка sanitizer
записывает только ответ локального тестового сервера во временный каталог и
проверяет отсутствие секретных/личных sentinel-значений. Политика записи ограничена
конкретным HTTP-контрактом; это не универсальный обезличиватель любых ответов API.
Расширение набора endpoints требует отдельного review формата и удаления приватных
данных до сохранения. Existing secret scan не отменяется.

## Этап 2: парсинг и модели — результат

| Библиотека | Версия / Python | Группа | Лицензия и зависимости | Решение |
|---|---|---|---|---|
| [TimeSense](https://pypi.org/project/timesense/1.1.0/) | 1.1.0 / >=3.9 | parsing-experiment | MIT; без обязательных зависимостей | Релиз 23.09.2026; production-замена отклонена по сравнению поведения |
| [cattrs](https://pypi.org/project/cattrs/26.2.1/) | 26.2.1 / >=3.10 | production | MIT; attrs >=25.4, typing-extensions >=4.14, exceptiongroup на Python <3.11 | Релиз 26.09.2026; преобразование архива существующих dataclasses |
| [msgspec](https://pypi.org/project/msgspec/0.22.0/) | 0.22.0 / >=3.10 | production | BSD-3-Clause; без обязательных runtime-зависимостей | Релиз 29.09.2026; структура и типы входящих LLM-предложений |

### TimeSense: эксперимент, без production-миграции

Реальный API: `TimeSenseParser(TimeConfig(timezone="Asia/Bishkek",
default_hour_for_one=9)).parse(text, now=aware_now, language="ru", tz=aware_now.tzinfo)`.
Результат может быть `TaskResult`, `ReminderResult`, `CalendarResult` или `None`.
[Исходный проект](https://github.com/kruatech/timesense) использует детерминированный
парсинг. Опциональные morph/tz extras не установлены.

`scripts/experiments/timesense_comparison.py` сравнивает 39 фиксированных сценариев
с действующими публичными parser contracts. Проверены русский язык, Asia/Bishkek,
относительные даты, граница года, leap day, неполные фразы, невалидные интервалы,
прошедшее время и пустое содержание. На Python 3.10 и 3.12 одинаково: 12 совпадений,
27 расхождений, включая четыре `None`. Совпадение означает сохранение темы, даты,
длительности и явного интервала либо точного reminder instant; отказ совпадает
только при отказе обоих вариантов.

Существенные расхождения: числовая длительность теряется в календарных фразах;
`в 09.10` интерпретируется иначе; «после обеда» даёт 13:00 вместо принятого 15:00,
«вечером» — 18:00 вместо 19:00; дата без времени может давать полночь вместо 09:00.
Некоторые прошедшие/пустые напоминания принимаются. Для сохранения контрактов
понадобилось бы оставить собственные ветки — deletion test не пройден.

Production-парсеры не изменены, заменённого кода нет (0 добавлено / 0 удалено).
Нет двух параллельных production-парсеров и нового fallback. Эксперимент запускается
отдельно, без сети, токенов или state:

```sh
python3 -m venv /tmp/dontanello-parsing-experiment
/tmp/dontanello-parsing-experiment/bin/python -m pip install -e '.[parsing-experiment]'
PYTHONPATH=src /tmp/dontanello-parsing-experiment/bin/python scripts/experiments/timesense_comparison.py
```

### cattrs: архив отчётов

`reports/adapters/sqlite_progress.py` использует
[`cattrs.preconf.json.make_converter`](https://catt.rs/en/stable/preconf.html)
для существующего `ProgressDocument` и вложенных dataclasses. Удалены ручные
словарные деревья `_encode`, вложенные конструкторы `_decode` и `_finding_dict`.
Основной поток: проверить schema version → нормализовать старые пустые metrics →
структурировать document. Единственный converter принадлежит адаптеру.

Synthetic fixture `tests/fixtures/progress_archive_v1.json` сохраняет
старый JSON. Contract tests сравнивают JSON objects, проверяют compact/unicode,
отсутствующие поля, неизвестные ключи, пустые metrics, чтение после перезапуска,
first-write-wins и Hypothesis round-trip вложенных metadata. Схема остаётся v1,
SQL, identity, порядок claim/save и исходные доменные модели не изменены.
Порядок ключей JSON не является контрактом; содержание, ISO даты и defaults сохраняются.

### msgspec: LLM-граница

`message_intent/adapters/groq.py` использует
[`msgspec.Struct` / `convert`](https://jcristharif.com/msgspec/structs.html):
Literal destination/confidence, positive integer minutes, форму clock и типы
selected schedule. Удалены `_minutes`, `_parse_due_date`, ручные проверки типов,
повторяющиеся множества допустимых значений и обходы словаря. Небольшие проверки
сочетаемости полей и stdlib date/time parsing остаются в адаптере; доменные правила
и выполнение действий по-прежнему принадлежат applications.

`json.loads` оставлен для прежней JSON-семантики, включая неиспользованные поля с
нестандартными числами; msgspec выполняет единственную структурную валидацию.
Невалидный selected schedule отклоняется, low-confidence и невыбранные schedules
не превращаются в действие и не обязаны проходить validation. Никакого repair,
повторного LLM-вызова или обхода confirmation не добавлено.

Hypothesis проверяет даты и длительности, а также отказ при boolean/string/float
вместо integer. Примеры проверяют явные интервалы, reminder daypart, конфликт полей,
невалидные часы, прежнюю ISO-форму `T1900`, low-confidence и нерелевантные nested fields. Существующие тесты
Telegram/calendar/reminders покрывают передаваемые typed intents и выполнение.

Сохраняется прежнее различие stdlib: basic ISO clock `T1900` принимается начиная
с Python 3.11; на Python 3.10 такой ответ отклоняется. Тест фиксирует оба варианта.

### Сравнение, сокращение и ограничения

Обе библиотеки проверены на реальном `ProgressDocument`: обе могут round-trip
модель, но cattrs JSON converter лучше сохраняет существующий ISO-формат datetime
(`+00:00`), тогда как msgspec JSON encoding может использовать `Z`. Поэтому архив
использует cattrs, LLM shape/type boundary — msgspec; обязанности не дублируются,
массового преобразования dataclasses нет.

Метрики сравниваются с рабочим деревом в начале этапа 2, включая предыдущие изменения,
а не с Git HEAD. Считались строки production-файлов, не тесты/документация/эксперимент.

| Замена | Добавлено / удалено | Размер файла до → после | Cognitive complexity до → после |
|---|---|---|---|
| cattrs archive | +11 / −132 | 304 → 183 | `_encode` 1 → 0, `_decode` 2 → 2; `_finding_dict` удалён |
| msgspec intent | +76 / −107 | 217 → 186 | `interpret` 18 → 13; `_time` 6 → 1; `_calendar_intent` 3 → 1; `_reminder_intent` 6 → 2 |
| Всего production | +87 / −239 | 521 → 369 | −152 строки net; score не является gate |

Новый риск: cattrs добавляет транзитивные dependencies и generated conversion code,
msgspec — нативное расширение. Обе версии зафиксированы; доступны и проверены на
Python 3.10/3.12. Неповреждённый старый архив поддержан; полностью произвольный
corrupt JSON не считается архивным контрактом. Новые исключения при повреждении
данных могут иметь иной тип/текст; внешние actions и их retry/recovery не изменены.
Полного dependency lockfile пока нет. Тесты работают offline на synthetic data;
production API и state не использовались. Независимый review не нашёл material findings.

## Следующие этапы: конкретные seams и deletion tests

Этапы 3–4 требуют повторной проверки актуальных первичных источников перед установкой.
Ни одна production-замена ниже ещё не выполнена.

### 3. SQLite и безопасные повторы

- **sqlite-utils 4 — проверено, production-миграция отклонена:** в `task_capture/adapters/sqlite.py`
  уже один `CREATE TABLE IF NOT EXISTS` с `UNIQUE` и доменным `CHECK(status IN ...)`;
  миграций пока нет. `table.create()` требует повторить описание колонок и ключа,
  но не выражает этот CHECK в заявленных аргументах API. Новый wrapper не удалит
  DDL, а миграционный механизм не заменит существующую потребность. Сохранён
  компактный stdlib SQL, атомарность, WAL и текущие database files; зависимость
  не добавлена. Переоценить, если появятся реальные версии схемы или несколько
  миграций. sqlite-utils 4.x требует Python >=3.10 и поддерживает 3.10/3.12.
- **stamina:** начать с read-only HTTP-операций в `integrations/notion/client.py`,
  сопоставив их текущий retry budget/таймауты. Deletion test: удаляется ли ручной
  retry loop для этих reads? Не повторять Notion/Telegram writes с uncertain outcome,
  не менять Groq failover только при явном 429 и учёт model-call budget.

### 4. Состояния и фоновые задачи

- **python-statemachine:** сравнить переходы `pending/sending/sent/uncertain` в
  `reports/delivery.py` и `task_capture/application.py`. Deletion test: исчезают ли
  повторные transition guards без дублирующего состояния поверх SQLite? Если нет,
  ограничить экспериментом. Хранилище и атомарный claim остаются источником состояния.
- **Huey:** изолированный прототип запуска безопасных read-only jobs вместо части
  `entrypoints/worker.py`; проверить schedules, timezone, restart и singleton lock.
  Не передавать ему production delivery/recovery без эквивалентных гарантий.
- **persist-queue:** отдельного текущего durable-queue сценария пока не установлено.
  Не дублировать журналы доставки или queue Huey. Рассматривать только независимую
  очередь read-only вычислений, если появится реальный producer/consumer контракт;
  отсутствие сценария — основание отказаться от production-установки.
- **json-repair:** синтаксические ошибки JSON на LLM-границе
  `message_intent/adapters/groq.py` / `reports/adapters/groq_progress.py`, сначала
  эксперимент с повреждёнными synthetic replies. После repair обязательны schema
  и бизнес-валидация; repaired ambiguity не разрешает внешнюю запись. Deletion test:
  можно ли удалить специальный syntax-retry, не ослабляя fail-closed поведение?
