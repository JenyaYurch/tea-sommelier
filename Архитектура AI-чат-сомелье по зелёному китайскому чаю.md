# Архитектурный документ: AI-чат-сомелье по зелёному китайскому чаю

**Статус:** MVP-план, готов к разработке
**Стек:** Google ADK Framework · Telegram · tea.support API · teashop.by (affiliate-каталог)
**Дата:** сентябрь 2026

---

## 1. Концепция и позиционирование

**Продукт:** специализированный AI-чат-сомелье по подбору зелёного китайского чая в Telegram.

**Ключевое позиционирование** — не общий чайный бот, а эксперт именно по зелёному китайскому чаю: сортам (Лунцзин, Би Ло Чунь, Аньцзи Бай Ча, Тай Пин Хоу Куй, Люань Гуапянь, Хуаншань Мао Фэн и др.), терруару, сезону сбора, градации, способу обработки (жарка vs пар).

**Сценарии:**
- Новичок выбирает первый Лунцзин
- Подбор под вкус / настроение / бюджет
- «Не хочу горечь» / «мягче» / «дешевле»
- Подарок
- «Что купить вместо сенчи/маття»
- Сравнение сортов бок-о-бок
- Точная заварка (температура, граммовка, время, проливы, посуда)

**Аудитория MVP:** русскоязычная, Польша, ЕС, tea geeks, экспаты.
**Не целевая аудитория (MVP):** материковый Китай (Telegram заблокирован; позже — WeChat mini-program).

**Референс подхода:** 京盛宇 AI 茶葉小博士 (Тайвань) — 24-часовой AI-консультант по чаю на Google Gemini, помогает подобрать чай, подарки, заварку. Адаптируем: расширяем энциклопедию (tea.support, 230 зелёных чаёв) + добавляем витрину партнёра (teashop.by) с реферальным кодом.

---

## 2. Архитектура (ADK-native)

Два Cloud Run сервиса — по официальному паттерну Google (codelab: ADK + Telegram, https://codelabs.developers.google.com/adk-cloudrun-telegram).

```
Telegram user
    │  сообщение боту
    ▼
Telegram Bot API  ──webhook──▶  telegram-integration (Cloud Run)
    │  python-telegram-bot[webhooks] + httpx
    │  • chat_id, typing-луп (ChatAction.TYPING каждые 4 сек)
    │  • маппинг Telegram user → ADK user_id/session_id
    │  • GET/POST сессии + POST /run к ADK-серверу
    ▼
tea-agent (Cloud Run)  — ADK API server
    │  app_name = "tea_sommelier"
    │  Runner + DatabaseSessionService (sqlite+aiosqlite)
    ▼
ADK-агент (LlmAgent, Gemini 2.5 Flash-Lite)
    │  function_tools → tea.support API + teashop.by каталог
    │  sub-agents (transfer) для онбординга и заварки
    │
    ├─▶ tea.support API  (https://api.thetea.app)  — слой ЗНАНИЙ
    │     /semantic  /tea/{slug}  /similar  /compare  /ask  /teas
    │
    └─▶ teashop.by каталог (JSON, курируемый) — слой ГДЕ КУПИТЬ
          slug ↔ название ↔ цена ↔ URL ↔ наличие
```

**Поток сообщения** (из codelab):
1. Webhook listener получает update в `handle_message()`.
2. Извлекает `user_message`, `chat_id`, `raw_user_id`.
3. Деривит ADK-идентификаторы: `user_id = tg_<raw_user_id>`, `session_id = tg_sess_<raw_user_id>`.
4. Стартует typing-луп (фон).
5. Проверяет/создаёт ADK-сессию: `GET /apps/{app}/users/{user}/sessions/{sess}` → если 404, `POST` с пустым телом.
6. Шлёт `POST /run` с payload `{appName, userId, sessionId, newMessage}`.
7. Берёт последний event, достаёт `event["content"]["parts"][0]["text"]`.
8. Останавливает typing-луп, отвечает в Telegram.

---

## 3. Слой данных

### 3.1. tea.support — слой знаний (энциклопедия)

**Что это:** REST API, гранулярная энциклопедия китайского чая.

**Проверено запросами к API:**
- 450 чаёв всего, из них **230 зелёных китайских чаёв** (категория `CHINA-GREEN TEA`, `origin_country=CN`).
- Эндпоинт `/api/v2/ask` (AI-сомелье) работает без ключа, принимает русский, возвращает ответ + источники. С параметром `lang=ru` отвечает по-русски.
- Эндпоинты: `/semantic` (поиск по «вайбу»), `/tea/{slug}` (карточка), `/similar` (похожие), `/compare` (сравнение), `/teas` (фильтр по type/country/province/brew_temp/altitude), `/glossary` (2623 термина, 72 языка).
- Поля карточки: вкус, заварка, терруар, химия, рецепты, координаты, `enrichment` (one-liner, tasting note, food pairing, caffeine, price tier), `seo`-блок.

**Тарифы (актуальные):**

| План | Цена | Лимит | Коммерч. |
|---|---|---|---|
| Free | $0 | 120 req/min, англ. | Нет |
| Pro | $4.99/мес | 600 req/min, 7 языков | Не указано |
| Expert | $9.99/мес | 5000 req/min, 72 языка | Да |
| Lifetime | $19.99 (до июля), $49.99 с июля | 5000 req/min | Да |

**Проверено smoke-тестом (реальные запросы):** `/teas?tea_type=green` (пагинация по 100 → 230 чаёв), `/ask` (с `lang=ru` отвечает по-русски).
**Подтверждены документацией, требуют smoke-test на неделе 1:** `/semantic`, `/tea/{slug}`, `/similar`, `/compare`. На запрос «сравни Лунцзин и Би Ло Чунь, что мягче» ответил, что эти чаи «не упоминаются» — не распознал знаменитые сорта по русским названиям и выдал случайные чаи. **Вывод:** tea.support = слой данных/retrieval, а не готовый сомелье. Сверху нужен свой LLM-диалоговый слой.

### 3.2. teashop.by — слой «где купить» (affiliate-каталог)

**Что это:** специализированный чайный магазин (Минск, BY), 500+ позиций, доставка в ЕС/РФ/США/Израиль.

**Проверено через браузер (первые 3 страницы, 90 из 122 товаров категории):**
- Найдено **53 китайских зелёных чая**: ~32 в наличии, ~21 нет в наличии.
- Полный каталог требует отдельного прохода (просмотрено 3 из ~4 страниц).
- Именно «флагманы», хорошо маппятся на slug'и tea.support (см. Приложение A).
- Цены в BYN, от 6.45 до ~29.50 BYN за минимальный вес; свежие сборы 2026 года.
- **Нет публичного affiliate/API/XML-фида; сайт блокирует ботов (403).**
- **Есть оптовый раздел [teashop.by/opt](https://www.teashop.by/opt/)** с формой запроса прайса и контактами: opt.teashop@gmail.com, +375(29)994-96-55, Telegram/Viber.

**Три варианта получения данных продуктов:**

| Вариант | Как | Плюс/минус |
|---|---|---|
| A. Партнёрство через opt-канал | Написать в opt-канал, договориться о реф-коде + выгрузке каталога (CSV/JSON) или API-доступе | Лучший: легально, стабильно, реальная комиссия. Нужно согласие |
| B. Ручной курируемый каталог | Один раз собрать 53 товара в JSON, периодически обновлять наличие через браузер | Быстрый старт без договорённостей; хрупко, нет комиссии без партнёрства |
| C. Авто-сбор (headless-браузер) | Плановый browser_task по категориям | ToS-риск и нестабильность |

**Рекомендация:** MVP стартует на варианте B (курируемый JSON), параллельно запускается трек A (партнёрство). После согласия — переход на A (стабильный фид + реферальный код).

### 3.3. RAG vs structured retrieval (важная правка)

tea.support **уже даёт retrieval** (`/semantic`, `/similar`). Полноценный RAG-стек нужен только для своих данных, которых у tea.support нет: авторские гайды по заварке, дегустационные заметки, обучающие материалы, ваша сомелье-логика.

**Критичное различие:**
- **teashop.by catalog — это structured retrieval/tool, не vector RAG.** Цены и наличие нельзя класть только в векторные чанки — модель может путаться и устаревать. Каталог = отдельный структурированный tool/фильтр с JSON-схемой.
- **Vector RAG — для гайдов, статей, tasting notes** (вариант B/C ниже).

| Вариант | Что в RAG | Стоимость | Когда |
|---|---|---|---|
| A. tea.support как retrieval | Ничего, используете их эндпоинты | $20–50 one-time | MVP, старта достаточно |
| B. Свой RAG (pgvector/Chroma) | Свои статьи, гайды, tasting notes | ~$0–5/мес | Когда нужен свой голос и точность |
| C. Vertex AI Search (managed) | Тот же контент, управляемый Google | $4/1000 запросов; 10 000/мес бесплатно | Масштаб |

**Рекомендация:** MVP — вариант A (tea.support) + structured tool для teashop.by. Свой vector RAG (B/C) добавляется, когда станет ясно, каких знаний не хватает.

---

## 4. Дизайн агента (ADK)

### Root-агент `tea_sommelier`

LlmAgent на Gemini 2.5 Flash-Lite. Держит диалог, анкету, профиль вкуса.

**Инструкция задаёт голос «сомелье по зелёному китайскому чаю»:**
- Ведёт анкету: опыт → вкус → бюджет → кофеин → посуда → страна покупки.
- Всегда обосновывает «почему».
- Даёт brewing guide (температура, граммовка, время, проливы, посуда, частые ошибки).
- Не выдумывает факты — всё через tools.
- Формирует 3 рекомендации с объяснением + кнопки «купить у партнёра».

### Function tools (вызывают tea.support + каталог)

| Tool | Источник | Зачем |
|---|---|---|
| `search_teas` | tea.support `/semantic` | Поиск по «вайбу»: «мягкий, без горечи, утро» |
| `get_tea_card` | tea.support `/tea/{slug}` | Карточка: вкус, заварка, терруар, food pairing, caffeine, price tier |
| `similar_teas` | tea.support `/tea/{slug}/similar` | Альтернативы «похожее, но мягче/дешевле» |
| `compare_teas` | tea.support `/compare` | «Лунцзин vs Би Ло Чунь» бок-о-бок |
| `resolve_tea` | локальный slug-словарь (230 чаёв) + `/teas?tea_type=green` | Распознавание русских названий сортов → slug (закрывает слабость `/ask`) |
| `find_in_shop` | teashop.by каталог (JSON) | Поиск товара по slug/сорту: цена, наличие, URL |
| `ask_sommelier` | tea.support `/ask` | Fallback, если всё остальное не сработало |

**Слой slug-словаря (230 зелёных чаёв)** — критичен: LLM сначала резолвит название сорта в slug через tool, потом тянет карточку. `/ask` оставляем как fallback.

### Sub-agents (через transfer)

- `onboarding_agent` — собирает профиль вкуса в 5–6 вопросов, сохраняет в state.
- `brewing_agent` — точная заварка: температура, граммовка, время, число проливов, тип посуды (гайвань/кружка), частые ошибки.

## 4.3. Хранилище сессий (важно)

SQLite — **только для локальной разработки/раннего прототипа**. Файловая система Cloud Run **не подходит** как надёжное долговременное хранилище при рестартах/масштабировании.

| Среда | Решение |
|---|---|
| Локально / dev | `sqlite+aiosqlite:///./sessions.db` (in-memory или локальный файл) |
| MVP с пользователями (прод) | **Cloud SQL Postgres**, Firestore, Redis/MemoryStore, **или Agent Engine session service** (`agentengine://`) |

Рекомендация: стартовать локально на SQLite, на деплое в Cloud Run — Cloud SQL Postgres или Agent Engine sessions.

### Сессии и память

- `DatabaseSessionService` (SQLite для dev / Cloud SQL Postgres для прод).
- `--session_service_uri` — `sqlite+aiosqlite:///./sessions.db` (dev) или `agentengine://<agent_engine>` (прод).
- `state` хранит: experience, taste_profile, budget, caffeine_pref, vessel, liked_teas[].
- Memory Bank (опционально, $0.25/1000 events) — долгосрочные предпочтения между сессиями.

---

## 5. Telegram-слой

Тонкая обёртка по codelab-паттерну: `python-telegram-bot[webhooks]` + `httpx`.

- `/start` + текстовый handler.
- Typing-луп каждые 4 сек.
- Webhook mode через `PORT`/`SERVICE_URL`; polling-режим для локальной разработки.
- Telegram inline-кнопки (`InlineKeyboard`) для «мягче / дешевле / без горечи / подарок / подробнее / купить» — генерируются агентом как часть ответа.

**Webhook URL:** `<SERVICE_URL>/<TELEGRAM_BOT_TOKEN>`

---

## 6. Деплой (Cloud Run, двухстадийный)

```bash
# 1. ADK-агент
adk deploy cloud_run --project=$GCP --region=europe-central2 \
  --service_name=tea-agent --with_ui tea_agent

# 2. Telegram-интеграция (две стадии: placeholder URL → реальный)
gcloud run deploy telegram-integration --source . \
  --region=$REGION --allow-unauthenticated \
  --set-env-vars "TELEGRAM_BOT_TOKEN=$BOT,ADK_SERVER_URL=$AGENT_URL,ADK_APP_NAME=tea_sommelier,SERVICE_URL=https://google.com"
SERVICE_URL=$(gcloud run services describe telegram-integration \
  --region=$REGION --format='value(status.url)')
gcloud run services update telegram-integration \
  --set-env-vars "...,SERVICE_URL=$SERVICE_URL"
```

**Референс-стартер:** https://github.com/alphinside/adk-a2a-agent-runtime-starter

**Структура проекта:**

```
tea-project/
├── tea_agent/                 # ADK-агент
│   ├── __init__.py
│   ├── agent.py              # root_agent (LlmAgent)
│   └── tools.py              # function_tools (tea.support + teashop.by)
├── data/
│   ├── green_teas_slugs.json  # slug-словарь 230 чаёв (tea.support)
│   └── teashop_catalog.json   # 53 товара teashop.by
├── telegram-integration/
│   ├── main.py                # webhook listener
│   ├── requirements.txt       # python-telegram-bot[webhooks], httpx
│   └── Dockerfile
├── requirements.txt
└── Dockerfile                 # для tea-agent
```

---

## 7. Стоимость

### Тарифы (актуальные на сентябрь 2026)

| Компонент | Цена | Бесплатный лимит |
|---|---|---|
| Agent Engine runtime | $0.0864 / vCPU-час | 50 vCPU-ч + 100 GB-ч / мес |
| Agent Engine память | $0.0090 / GB-час | (в free tier) |
| Sessions / Memory Bank | $0.25 / 1000 events | — |
| Vertex AI Search | $4 / 1000 запросов | 10 000 / мес |
| Cloud Run | ~$0.000024 / vCPU-сек | 2M запросов + 360k vCPU-сек / мес |
| Gemini 2.5 Flash-Lite (до 16 окт 2026) | $0.10 вход / $0.40 выход за 1M токенов | есть free tier |
| Gemini 3 Flash | $0.50 / $3.00 за 1M | — |
| tea.support | $20–50 one-time (verify current price) | — |

Источники: https://www.cloudzero.com/blog/google-vertex-ai-pricing/ , https://uibakery.io/blog/vertex-ai-agent-builder , https://adk.dev/deploy/agent-engine/

### Сценарии (типичный диалог: ~7500 входных + 2500 выходных токенов на пользователя)

| Сценарий | 500 польз. / 2000 диалогов | 5000 польз. / 20 000 диалогов |
|---|---|---|
| Gemini 2.5 Flash-Lite | ~$3.5/мес | ~$35/мес |
| Gemini 3 Flash | ~$22/мес | ~$220/мес |
| Agent Engine runtime (low traffic) | $0 (free tier) | ~$5–15/мес |
| Vertex AI Search (10k бесплатно) | $0 | ~$4–8/мес |
| Cloud Run хостинг | $0 (free tier) | ~$2–5/мес |
| tea.support | $20–50 разово | $20–50 разово |
| **Итого/мес** | **~$5–25** | **~$60–250** |

Плюс **$300 бесплатных кредитов Google Cloud** новым аккаунтам на 90 дней.

---

## 8. План на 30 дней

| Неделя | Задача |
|---|---|
| **1** | Локально: `tea_agent/agent.py` (root LlmAgent) + function tools к tea.support; slug-словарь 230 чаёв в локальный кэш. `adk run` — прогон сценариев в CLI. |
| **2** | Sub-agents (onboarding, brewing), state-профиль, инструкция-сомелье. Инлайн-кнопки в ответах. Подключить teashop.by каталог как tool. |
| **3** | `telegram-integration/main.py` по codelab-паттерну; двухстадийный деплой на Cloud Run; webhook. |
| **4** | Закрытая бета 30–50 пользователей; метрика «нашёл чай → купил». |

**Параллельный трек — партнёрство:** на неделе 1 написать в opt-канал teashop.by предложение реферального партнёрства + запрос каталога/промо-кода.

---

## 9. Монетизация

- **Free:** 5–10 рекомендаций, базовые гайды.
- **Premium:** персональный профиль вкуса, подбор под бюджет, история, дегустационный дневник.
- **Affiliate:** ссылки на чайные магазины (teashop.by — русскоязычная/ЕС; позже 1–2 ЕС-партнёра для ценовой альтернативы).
- **Позже:** curated tasting sets, B2B white-label бот для чайных магазинов.

**Главное:** без партнёрства с teashop.by реальной комиссии не будет — нужен agreement по опт-каналу для реф-кода и/или фида каталога.

---

## 10. Риски и митигация

| Риск | Митигация |
|---|---|
| Распознавание сортов по-русски (слабость `/ask`) | slug-словарь + tool `resolve_tea`; не полагаться на `/ask` |
| Telegram не для материкового Китая | MVP на русско/ЕС; WeChat mini-program вторым этапом |
| Нет API/фида у teashop.by | Партнёрство через opt-канал; курируемый JSON как fallback |
| Коммерч. условия tea.support не детализированы | Запросить у автора условия перед продакшеном |
| Каталог меняется (цены/наличие/сборы) | Периодическое обновление (раз в 1–2 недели) через браузер или фид |
| ADK — молодой фреймворк | codelab + starter-репо снижают риск; шероховатости возможны |

---

## Приложение A: Маппинг teashop.by → tea.support (в наличии, из проверенных)

> Просмотрено 3 из ~4 страниц категории. 53 китайских зелёных чая: ~32 в наличии, ~21 нет в наличии. Полный список — в `data/teashop_catalog.json` (собирается на неделе 1).

| teashop.by (в наличии) | tea.support slug |
|---|---|
| Си Ху Лун Цзин, 2026 | `longjing` (Си Ху) |
| Дунтин Би Ло Чунь, Цзянсу | `biluochun` |
| Аньцзи Бай Ча, 2026 | `anji-baicha` |
| Тай Пин Хоу Куй, 2026 | `taiping-houkui` |
| Люань Гуапянь, 2026 | `liu-an-gua-pian` |
| Чжу Е Цин, 2026 | `zhu-ye-qing` |
| Мэндин Гань Лу, 2026 | `mengding-ganlu` |
| Синь Ян Мао Цзянь, 2026 | `xinyang-maojian` |
| Лушань Юнь У, 2026 | `lushan-yunwu` |
| Е Шен Люй Ча (БаДу Чай), 2026 | `yesheng-lvcha` |
| Би Ло Чунь Гуи, 2026 | `biluochun-gui` |

> **Нет в наличии** (для справки): Хуаншань Мао Фэн (2023/2024), Лунцзин Тоу Чунь 2026, У Ню Цзао Лунцзин 2026, Тайпин Хоукуй 2023 и др. — см. полный список в `teashop_catalog.json`.

## Приложение B: JSON-схемы данных

### `data/green_teas_slugs.json`
```json
{
  "slug": "biluochun",
  "name_en": "Biluochun",
  "name_ru": "Би Ло Чунь",
  "name_zh": "碧螺春",
  "pinyin": "bìluóchūn",
  "aliases": ["Билочунь", "Bi Luo Chun", "Дунтин Би Ло Чунь"],
  "tea_type": "green",
  "origin_country": "CN",
  "category_code": "CHINA-GREEN TEA",
  "province": null
}
```

### `data/teashop_catalog.json`
```json
{
  "product_name": "Дунтин Би Ло Чунь, Цзянсу, весна 2026",
  "matched_slug": "biluochun",
  "price_from_byn": 29.50,
  "availability": "in_stock",
  "harvest_year": 2026,
  "category_url": "https://www.teashop.by/shop/chaj/zeleniy/",
  "product_url": "...",
  "source_page": 1,
  "last_checked": "2026-09-07",
  "mapping_confidence": "high"
}
```

## Приложение C: Переменные окружения / секреты

| Переменная | Назначение |
|---|---|
| `TELEGRAM_BOT_TOKEN` | токен бота от BotFather |
| `GOOGLE_API_KEY` / `GOOGLE_CLOUD_PROJECT` | auth для Gemini/Vertex |
| `TEA_SUPPORT_API_KEY` | Bearer ключ tea.support (опц., Free без него) |
| `ADK_SERVER_URL` | URL tea-agent Cloud Run сервиса |
| `ADK_APP_NAME` | `tea_sommelier` |
| `SERVICE_URL` | URL telegram-integration Cloud Run сервиса |

**Хранить в Google Secret Manager, не коммитить `.env`.**

## Приложение D: Приёмочные тесты / regression prompts

- «Я новичок, хочу мягкий чай без горечи утром»
- «Сравни Лунцзин и Би Ло Чунь, что мягче»
- «Подарок до 20 евро»
- «У меня только кружка, как заварить?»
- «Хочу похожий на сенчу, но китайский»
- «Покажи, что можно купить у партнёра»

## Приложение E: Политика источников

- **tea.support** = факты о чае: вкус, заварка, терруар, химия, сравнения.
- **teashop.by** = цена, наличие, ссылка на покупку.
- **При конфликте** — не смешивать; цена/наличие только из каталога магазина.
- **Без медицинских обещаний** по здоровью/кофеину — только информационно, при необходимости отсылать к врачу.

## Приложение F: tea.support — ключевые эндпоинты

| Эндпоинт | Функция |
|---|---|
| `GET /api/v2/teas?tea_type=green` | Список зелёных чаёв (230 шт., пагинация по 100) |
| `GET /api/v2/tea/{slug}` | Полная карточка чая |
| `GET /api/v2/semantic` | Семантический поиск по «вайбу» |
| `GET /api/v2/tea/{slug}/similar` | Похожие чаи (сенсорные векторы) |
| `GET /api/v2/compare` | Сравнение бок-о-бок |
| `GET /api/v2/ask` | AI-сомелье (fallback) |
| `GET /api/v2/glossary` | 2623 терминов, 72 языка |

Аутентификация: `Authorization: Bearer tt_…` или `?key=…`. Free tier — без ключа, англ. только.

## Приложение G: Источники

- tea.support API: https://tea.support/ , https://api.thetea.app/
- ADK deploy: https://adk.dev/deploy/agent-engine/ , https://adk.dev/deploy/cloud-run/
- Codelab ADK + Telegram: https://codelabs.developers.google.com/adk-cloudrun-telegram
- Starter-репо: https://github.com/alphinside/adk-a2a-agent-runtime-starter
- teashop.by: https://www.teashop.by/ , опт: https://www.teashop.by/opt/
- Референс подхода: https://www.jsy-tea.com/pages/ai-tea-consultant
- Конкуренты (обзор): TeeGschwendner AI Advisor (https://www.reply.com/en/cx-and-digital-commerce/teegschwendner-ai-powered-tea-advisor), FindMeTea (https://findmetea.com/), Resteeped (https://apps.apple.com/us/app/resteeped-discover-tea/id6758778808), SteepSense (https://steepsense.com/), SleekAI (https://sleekwp.com/ai/chatbot-for/specialty-tea-shops/), KETLI (https://www.ketli.in/about)
- Цены Google Cloud: https://www.cloudzero.com/blog/google-vertex-ai-pricing/ , https://uibakery.io/blog/vertex-ai-agent-builder
