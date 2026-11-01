# MAX с российского сервера (около 1–1,5 часа)

Заменяет ветку v3.2 в n8n: в Нидерландах ничего не меняется, там работает v3.1 и публикует только Telegram.

## Как устроено

```
outbox/ ──n8n (Нидерланды)──► Telegram ──► sent/{id}.json
                                                │
          российский сервер, раз в 2 минуты ◄───┘
          текст и картинка из истории outbox/ ──► канал MAX ──► sent-max/{id}.json
                                                   ошибка ──► failed-max/ + сообщение в Telegram
```

- В MAX уходит только то, что уже вышло в Telegram. Пост на паузе или с ошибкой Telegram в MAX не попадёт.
- Каналы сопоставляются в `config.json`: канал Telegram → `chat_id` канала MAX. Пустое значение — не дублировать.
- `"max": false` в файле поста — не дублировать; `"max_text_html"` — свой текст для MAX.
- Повтор один раз через 10 секунд и только при `attachment.not.ready`: тогда поста в MAX точно нет. Любая другая ошибка без повтора, чтобы не было дубля.
- Защита от дублей: отметка на сервере ставится сразу после ответа MAX, до записи в GitHub. Если GitHub в этот момент недоступен, запись дозаписывается при следующем проходе.
- Посты, вышедшие в Telegram больше 12 часов назад (`max_lag_hours`), не публикуются, а уходят в `failed-max/`. Если сервер долго лежал, в канал не выйдет пачка старых постов.
- Посты раньше `start_from` не трогаются: старые посты в MAX не переносятся.
- Без n8n: один файл на Python, только стандартная библиотека, запуск через systemd. Обновлять n8n на этом сервере не нужно.

Где выполнять: **Сервер** — SSH на российском сервере; **Компьютер** — у вас; **MAX** — приложение или business.max.ru; **GitHub** — сайт github.com.

## Шаг 0. До начала (MAX и GitHub, 15 минут)

1. **MAX.** Бот в статусе «Опубликован», токен скопирован (business.max.ru → Чат-боты).
2. **MAX.** Бот добавлен администратором канала «Человек vs ИИ» с правом писать посты.
3. **GitHub.** Settings → Developer settings → Fine-grained tokens → Generate. Repository access: только `drokhman/channels-publisher`. Permissions → Contents: Read and write. Срок — 1 год. Сохраните токен в менеджере паролей.

## Шаг 1. Сервер и доступ (15–20 минут)

Установлено 01.11.2026 на московский `n8n-channels` `5.42.97.12` (вход по root-паролю из панели Timeweb; ключа с рабочего компьютера на нём нет). Время на сервере — UTC. Иначе создайте в Timeweb самый дешёвый сервер в Москве или Санкт-Петербурге с Ubuntu: публикатору хватает 1 ГБ памяти.

1. **Компьютер.** `ssh root@IP_сервера`
2. **Сервер.** Сертификат Минцифры и сверка отпечатка:
   ```sh
   mkdir -p /opt/max-publisher && cd /opt/max-publisher
   curl -fsSL -o russian_trusted_root_ca.pem https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt
   openssl x509 -in russian_trusted_root_ca.pem -noout -fingerprint -sha256
   ```
   Проверка: `D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31`. Если отпечаток другой — остановитесь.
3. **Сервер.** Доступ к трём сервисам:
   ```sh
   curl -s -o /dev/null -w "GitHub %{http_code}\n" https://api.github.com
   curl -s -o /dev/null -w "MAX %{http_code}\n" --cacert russian_trusted_root_ca.pem https://platform-api2.max.ru/me
   curl -s -o /dev/null -w "Telegram %{http_code}\n" --max-time 15 https://api.telegram.org
   ```
   Проверка: GitHub `200`, MAX `401` (без токена это нормально, главное — не `000`). Telegram может не ответить: он нужен только для уведомлений об ошибках.

## Шаг 2. Установка (15–20 минут)

1. **Сервер.** Пользователь и папка состояния:
   ```sh
   useradd --system --no-create-home --shell /usr/sbin/nologin maxpub
   mkdir -p /var/lib/max-publisher && chown maxpub:maxpub /var/lib/max-publisher
   ```
2. **Сервер.** Токены. Ввод не отображается на экране, в историю команд они не попадают:
   ```sh
   cd /opt/max-publisher
   read -rsp "GitHub токен: " G; echo; read -rsp "MAX токен: " M; echo; read -rsp "Telegram токен (Enter — без уведомлений): " T; echo
   printf 'GITHUB_TOKEN=%s\nMAX_TOKEN=%s\nTELEGRAM_TOKEN=%s\n' "$G" "$M" "$T" > .env; unset G M T
   chown root:maxpub .env && chmod 640 .env
   ```
   Telegram-токен — того же бота @masterorservant, он пишет вам об ошибках.
3. **Сервер.** Файлы из репозитория:
   ```sh
   set -a; . ./.env; set +a
   for f in max_publisher.py config.example.json max-publisher.service max-publisher.timer; do
     curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" -H "Accept: application/vnd.github.raw+json" \
       -o $f https://api.github.com/repos/drokhman/channels-publisher/contents/max-publisher/$f; done
   unset GITHUB_TOKEN MAX_TOKEN TELEGRAM_TOKEN
   [ -f config.json ] || cp config.example.json config.json; ls -l
   ```
   Проверка: шесть файлов (`.env` в списке не виден). Ответ `curl: (22) … 404` — у токена GitHub нет доступа к репозиторию: в настройках токена выберите `drokhman/channels-publisher` и Contents: Read and write.
4. **Сервер.** Проверка связи и `chat_id` канала:
   ```sh
   systemd-run --quiet --pipe --wait -p User=maxpub -p EnvironmentFile=/opt/max-publisher/.env \
     /usr/bin/python3 /opt/max-publisher/max_publisher.py --check
   ```
   Проверка: `GitHub: OK`, `MAX /me: OK, бот …` и строки `событие: chat_id=…`. Отрицательное число с `bot_added` — ID канала, положительное с `bot_started` — ваш личный диалог с ботом. Список чатов (`GET /chats`) MAX отключил в июне 2026, ID приходит только в событиях бота. Если событий нет — уберите бота из администраторов и подписчиков канала, добавьте заново и повторите `--check`.

   Канал «Человек vs ИИ»: `chat_id = -80727820025148` (получено 01.11.2026).
5. **Сервер.** `nano config.json`: в `"@chelovek_vs_ai"` впишите `chat_id` (в кавычках), `start_from` — текущие дата и время по Москве, например `"2026-11-01T15:00"`. `enabled` пока оставьте `false`. Сохраните: Ctrl+O, Enter, Ctrl+X.
6. **Сервер.** Пробный проход, ничего не отправляет:
   ```sh
   systemd-run --quiet --pipe --wait -p User=maxpub -p EnvironmentFile=/opt/max-publisher/.env \
     /usr/bin/python3 /opt/max-publisher/max_publisher.py --dry-run
   ```
   Пока после `start_from` ничего не вышло, вывод пустой — это нормально.
7. **Сервер.** Включение:
   ```sh
   sed -i 's/"enabled": false/"enabled": true/' config.json
   cp max-publisher.service max-publisher.timer /etc/systemd/system/
   systemctl daemon-reload && systemctl enable --now max-publisher.timer
   systemctl list-timers max-publisher.timer
   ```
   Проверка: в списке таймер со следующим запуском через 1–2 минуты.

## Шаг 3. Проверка на первом посте (10 минут)

1. Дождитесь ближайшего поста «Человек VS ИИ» в Telegram. Через 2–4 минуты он должен появиться в MAX.
2. **Сервер.** Журнал: `journalctl -u max-publisher --since "1 hour ago" --no-pager`
   Проверка: строка `… → MAX опубликован https://max.ru/…`, в GitHub появился `sent-max/{id}.json`.
3. **MAX.** Сверьте жирный текст, ссылки, переносы строк, картинку над текстом. Если что-то не так — сообщите ассистенту.
4. После 2–3 удачных постов можно добавить `chat_id` второго канала в `config.json` (без перезапуска).

## Откат и обслуживание

- Выключить: `systemctl disable --now max-publisher.timer`. Telegram продолжит работать как прежде.
- Пост попал в `failed-max/` и его надо выпустить: исправьте причину, удалите отметку `rm /var/lib/max-publisher/done/{id}.json` и файл в `failed-max/` в GitHub. Пост уйдёт, если с публикации в Telegram прошло меньше 12 часов. Иначе — вручную в MAX.
- Обновить скрипт: повторите шаг 2.3 (`config.json` и `.env` не перезаписываются).
- Перевыпуск токенов: заново шаг 2.2.
- Ветку MAX в v3.2 на сервере в Нидерландах не включать: иначе посты уйдут в MAX дважды.

## Что проверено

- На имитации GitHub и MAX: картинка из файла (с первым ответом «не готово» и повтором), картинка по ссылке, ошибка MAX, длинный текст, `"max": false`, опоздание больше 12 часов, канал без сопоставления, посты до `start_from`, падение GitHub на записи (дозапись следующим проходом), повторный проход без дублей.
- На настоящем репозитории: текст и картинка вышедшего поста достаются из истории `outbox/`.
- Живой API MAX с российского сервера ещё не проверялся — это шаг 2.4.

## Уведомления об ошибках

Сначала в личный диалог с ботом в MAX (`admin_max_user_id`, ваш ID `568370947` — из события `bot_started`), затем в Telegram (`admin_chat_id`). Из России Telegram отвечает не всегда, поэтому основной канал уведомлений — MAX. Проверка: `max_publisher.py --check --notify-test`.

## Канал «Он рядом» (@ryadom_repetitor)

Подключён 01.11.2026: «Он Рядом | репетиторам: ученики, родители, ИИ», `chat_id -80764600991036`, создан в приложении MAX. Посты берутся из `sent/`, как у «Человек VS ИИ».

## Канал НППЦП (@nscspr)

Посты @nscspr публикует не n8n, а GitHub Actions (`ucp-schedule.yml`). Публикатор берёт их из `scheduled-ucp-done/{id}.json`: там полный текст и путь к картинке в `commands-ucp-media/`. Запись в `sent-max/` — с приставкой `ucp-`.

- Дублируются посты с картинкой и текстом, если в Telegram все шаги прошли успешно.
- Опросы не переносятся: в Bot API MAX создания опросов нет. Пост из одного опроса пропускается без ошибки.
- `"max": false` в файле — не дублировать; `max_text_html` в шаге — свой текст для MAX.
- Промо-посты ведут на `t.me/chelovek_vs_ai/…`: в MAX ссылка останется на Telegram.
- Подключение: `"@nscspr": "<chat_id>"` в `channels` и `"start_from_ucp"` — время по Москве, с которого начинать. Пустое значение — канал не дублируется.

Подключён 01.11.2026: «Национальная платформа поддержки ЦП», `chat_id -80763895561532`, `start_from_ucp 2026-11-01T13:57`.

Самозанятому на платформе MAX доступен один канал, он занят «Человек vs ИИ». Канал НППЦП создаётся в приложении MAX (до 5 публичных каналов).
