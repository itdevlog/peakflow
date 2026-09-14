#!/usr/bin/env bash
# manage.sh — установка, обновление и эксплуатация Telegram-бота пикфлоуметрии
# Использование: ./manage.sh <команда> [аргументы]
# Быстрая установка с нуля (без ручного клонирования):
#   curl -fsSL https://raw.githubusercontent.com/itdevlog/peakflow/main/manage.sh | bash -s -- install
set -Eeuo pipefail

REPO_URL="https://github.com/itdevlog/peakflow.git"
INSTALL_DIR_DEFAULT="/opt/peakflow"

if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
    SCRIPT_DIR=""   # запущен из пайпа (curl | bash) — репозитория рядом нет
fi

VENV_DIR="${SCRIPT_DIR:-.}/.venv"
ENV_FILE="${SCRIPT_DIR:-.}/.env"
ENV_EXAMPLE="${SCRIPT_DIR:-.}/.env.example"
BACKUP_DIR="${SCRIPT_DIR:-.}/backups"
LOG_DIR="${SCRIPT_DIR:-.}/logs"
HEALTH_TIMEOUT=30

# --- Инстансы: несколько ботов на одном сервере -------------------------------
# Имя инстанса задаёт имена systemd-юнита, PID-файла и Caddy-фрагмента, чтобы
# боты в разных каталогах не перезаписывали сервисы и конфиг друг друга.
# Приоритет: --instance NAME > RASPISANIE_INSTANCE > basename каталога установки.
LEGACY_SERVICE_NAME="peakflow-bot"
CADDY_DIR="/etc/caddy"
CADDY_MAIN="${CADDY_DIR}/Caddyfile"

sanitize_instance() {
    printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_-' '-' \
        | sed -e 's/^-\+//' -e 's/-\+$//'
}

init_instance() {
    local raw="${1:-${RASPISANIE_INSTANCE:-}}"
    [[ -n "$raw" ]] || raw="$(basename "${SCRIPT_DIR:-peakflow}")"
    INSTANCE="$(sanitize_instance "$raw")"
    [[ -n "$INSTANCE" ]] || INSTANCE="peakflow"
    SERVICE_NAME="${LEGACY_SERVICE_NAME}-${INSTANCE}"
    SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
    PID_FILE="${SCRIPT_DIR:-.}/bot.${INSTANCE}.pid"
    CADDY_SITE="${CADDY_DIR}/conf.d/${INSTANCE}.caddy"
}
init_instance

# Источник интерактивного ввода. При curl | bash stdin занят телом скрипта,
# поэтому вопросы читаем напрямую с терминала. Нет tty — пусто (ответы = дефолт).
INPUT_FD=""
if [[ -t 0 ]]; then
    INPUT_FD="/dev/stdin"
elif { exec 9< /dev/tty; } 2>/dev/null; then
    exec 9<&-
    INPUT_FD="/dev/tty"
fi

is_repo() {
    [[ -n "$SCRIPT_DIR" ]] \
        && [[ -f "${SCRIPT_DIR}/requirements.txt" ]] \
        && [[ -f "${SCRIPT_DIR}/.env.example" ]] \
        && git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1
}

# --- Цветной вывод -----------------------------------------------------------
set_colors() {
    # set_colors <on|off> — off, если NO_COLOR задан (любым значением) или передан --no-color
    if [[ "$1" == "off" || -n "${NO_COLOR:-}" ]]; then
        C_OK=""; C_ERR=""; C_WARN=""; C_INFO=""; C_DIM=""; C_OFF=""
    else
        C_OK=$'\033[32m'; C_ERR=$'\033[31m'; C_WARN=$'\033[33m'
        C_INFO=$'\033[36m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
    fi
}
set_colors on

ok()   { printf '%s[OK]%s %s\n' "$C_OK" "$C_OFF" "$*"; }
fail() { printf '%s[FAIL]%s %s\n' "$C_ERR" "$C_OFF" "$*" >&2; }
warn() { printf '%s[WARN]%s %s\n' "$C_WARN" "$C_OFF" "$*"; }
info() { printf '%s[INFO]%s %s\n' "$C_INFO" "$C_OFF" "$*"; }
dim()  { printf '%s%s%s\n' "$C_DIM" "$*" "$C_OFF"; }
die()  { fail "$*"; exit 1; }

env_get() {
    # env_get <KEY> — значение из .env или пусто
    [[ -f "$ENV_FILE" ]] || return 1
    grep -E "^${1}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true
}

token_configured() {
    local token
    token=$(env_get BOT_TOKEN)
    [[ -n "$token" && "$token" != *"YOUR"* && "$token" != *"123456789:ABCdef"* ]]
}

get_port() {
    local port
    port=$(env_get WEBAPP_PORT)
    if ! grep -qE '^WEBAPP_PORT=' "$ENV_FILE" 2>/dev/null; then
        echo "8080"
    elif [[ "$port" =~ ^[0-9]+$ ]]; then
        echo "$port"
    else
        echo "0"
    fi
}

get_db_file() {
    local db
    db=$(env_get DB_PATH)
    db="${db:-peakflow.db}"
    if [[ "$db" = /* ]]; then
        echo "$db"
    else
        echo "${SCRIPT_DIR:-.}/${db}"
    fi
}

get_webapp_url() {
    env_get WEBAPP_URL
}

get_webapp_domain() {
    local url
    url=$(get_webapp_url)
    url="${url#http://}"
    url="${url#https://}"
    url="${url%%/*}"
    echo "$url"
}

systemd_available() { command -v systemctl >/dev/null 2>&1; }
service_exists() { systemd_available && systemctl list-unit-files "${SERVICE_NAME}.service" --no-legend 2>/dev/null | grep -q .; }
service_active() { service_exists && systemctl is-active --quiet "${SERVICE_NAME}.service"; }

run_root() {
    if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        die "Нужны права root (запустите под root или установите sudo)"
    fi
}

system_pm() {
    local pm
    for pm in apt-get dnf yum apk; do
        command -v "$pm" >/dev/null 2>&1 && { echo "$pm"; return 0; }
    done
    return 1
}

install_pkgs() {
    [[ $# -gt 0 ]] || return 0
    local pm
    pm=$(system_pm) || die "Пакетный менеджер не найден — установите вручную: $*"
    case "$pm" in
        apt-get) run_root apt-get update -qq && run_root apt-get install -y -qq "$@" >/dev/null ;;
        dnf)     run_root dnf install -y -q "$@" ;;
        yum)     run_root yum install -y -q "$@" ;;
        apk)     run_root apk add --quiet "$@" ;;
    esac
}

ensure_cmd() {
    local cmd="$1"; shift
    command -v "$cmd" >/dev/null 2>&1 && return 0
    warn "'${cmd}' не найден — потребуется установить: $*"
    confirm "Установить ($*)?" y || die "'${cmd}' обязателен. Установите вручную: $*"
    install_pkgs "$@" || die "Не удалось установить: $*"
    command -v "$cmd" >/dev/null 2>&1 || die "Команда '${cmd}' всё ещё недоступна после установки"
    ok "Установлено: $*"
}

service_running() {
    service_active && return 0
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        return 0
    fi
    external_pid >/dev/null
}

external_pid() {
    local pid dir_pattern
    for pid in $(pgrep -f "python.*bot\.py" 2>/dev/null || true); do
        dir_pattern=$(readlink "/proc/${pid}/cwd" 2>/dev/null || true)
        if [[ "$dir_pattern" == "$SCRIPT_DIR" ]]; then
            echo "$pid"
            return 0
        fi
    done
    return 1
}

health_check() {
    # Успех если бот отвечает /healthz. При WEBAPP_PORT=0 — только проверка процесса.
    local port="$1" waited=0
    if [[ "$port" == "0" ]]; then
        service_running
        return $?
    fi
    while (( waited < HEALTH_TIMEOUT )); do
        if curl -sf "http://localhost:${port}/healthz" >/dev/null 2>&1; then
            return 0
        fi
        service_running || return 1
        sleep 2; waited=$((waited + 2))
    done
    return 1
}

ask() {
    local default="$1"; shift
    local reply=""
    if [[ -n "$INPUT_FD" ]]; then
        read -r -p "$* ${C_DIM}[${default:-пусто}]:${C_OFF} " reply < "$INPUT_FD" || reply=""
    fi
    REPLY="${reply:-$default}"
}

confirm() {
    local prompt="$1" default="${2:-y}" reply=""
    if [[ -n "$INPUT_FD" ]]; then
        read -r -p "${prompt} [${default^^}] " reply < "$INPUT_FD" || reply=""
    fi
    reply="${reply:-$default}"
    [[ "${reply,,}" == "y" || "${reply,,}" == "да" ]]
}

# --- install / service --------------------------------------------------------
migrate_legacy_service() {
    # Установки до поддержки мульти-инстанса держали единый юнит
    # <legacy>.service. Если он указывает на наш каталог — заменяем его на
    # инстансный, иначе не трогаем (это service другого бота).
    local legacy_file="/etc/systemd/system/${LEGACY_SERVICE_NAME}.service"
    [[ -f "$legacy_file" ]] || return 0
    local wd
    wd=$(grep -E '^WorkingDirectory=' "$legacy_file" 2>/dev/null | cut -d= -f2- || true)
    [[ "$wd" == "$SCRIPT_DIR" ]] || return 0
    info "Найден старый сервис ${LEGACY_SERVICE_NAME}.service для этого каталога — переношу в ${SERVICE_NAME}.service"
    systemctl stop "${LEGACY_SERVICE_NAME}.service" >/dev/null 2>&1 || true
    systemctl disable "${LEGACY_SERVICE_NAME}.service" >/dev/null 2>&1 || true
    rm -f "$legacy_file"
    systemctl daemon-reload 2>/dev/null || true
}

install_service() {
    migrate_legacy_service
    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Telegram Peakflow Bot (${INSTANCE})
After=network.target

[Service]
Type=simple
User=${USER:-$(id -un)}
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${VENV_DIR}/bin/python bot.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload || die "systemctl daemon-reload не сработал"
    if token_configured; then
        systemctl enable --now "${SERVICE_NAME}.service" \
            || die "Не удалось запустить сервис. Проверьте: systemctl status ${SERVICE_NAME}"
        ok "systemd-сервис установлен и запущен (автозапуск включён)"
    else
        systemctl enable "${SERVICE_NAME}.service" >/dev/null \
            || die "Не удалось включить автозапуск сервиса"
        warn "Сервис установлен, но НЕ запущен: токен в .env не задан"
        info "Впишите BOT_TOKEN в ${ENV_FILE}, затем: ./manage.sh start"
    fi
    dim "Статус: systemctl status ${SERVICE_NAME}.service"
}

cmd_install() {
    if ! is_repo; then
        die "В ${SCRIPT_DIR:-текущем каталоге} нет репозитория бота (requirements.txt / .env.example).
Клонируйте: git clone ${REPO_URL}
Или используйте быструю установку: curl -fsSL <REPO>/manage.sh | bash -s -- install"
    fi
    info "Установка бота в ${SCRIPT_DIR}"

    ensure_cmd git git
    ensure_cmd curl curl

    local py_bin=""
    for p in python3.13 python3.12 python3.11 python3; do
        if command -v "$p" >/dev/null 2>&1; then
            "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null && { py_bin="$p"; break; }
        fi
    done
    if [[ -z "$py_bin" ]]; then
        fail "Python 3.11+ не найден"
        local pm
        pm=$(system_pm) || die "Установите Python 3.11+ вручную: https://python.org"
        case "$pm" in
            apt-get) install_pkgs python3 python3-venv python3-pip ;;
            dnf|yum) install_pkgs python3 python3-pip ;;
            apk)     install_pkgs python3 py3-pip ;;
        esac || die "Не удалось установить Python"
        for p in python3.13 python3.12 python3.11 python3; do
            command -v "$p" >/dev/null 2>&1 && { py_bin="$p"; break; }
        done
        [[ -n "$py_bin" ]] || die "Python 3.11+ не найден после установки — обновите дистрибутив"
    fi
    ok "Python: $("$py_bin" --version)"

    if ! "$py_bin" -c 'import venv' 2>/dev/null; then
        warn "Модуль venv недоступен — устанавливаю"
        install_pkgs python3-venv || install_pkgs python3 || die "Установите python3-venv вручную"
        "$py_bin" -c 'import venv' 2>/dev/null || die "Модуль venv всё ещё недоступен"
    fi

    if [[ -d "$VENV_DIR" ]]; then
        info "Виртуальное окружение уже существует — обновляю пакеты"
    else
        "$py_bin" -m venv "$VENV_DIR" || die "Не удалось создать venv в ${VENV_DIR}"
    fi
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip \
        || warn "Не удалось обновить pip (не критично)"
    "$VENV_DIR/bin/pip" install --quiet -r "${SCRIPT_DIR}/requirements.txt" \
        || die "Не удалось установить зависимости"
    ok "Зависимости установлены"

    mkdir -p "$LOG_DIR" "$BACKUP_DIR" "${SCRIPT_DIR}/data"
    ok "Директории data/ logs/ backups/ готовы"

    if [[ -f "$ENV_FILE" ]]; then
        info "Файл .env уже существует — пропускаю настройку"
    elif [[ -z "$INPUT_FD" ]]; then
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        warn "Скопирован .env.example -> .env (терминала нет — интерактив пропущен)"
        warn "Вставьте токен бота: nano ${ENV_FILE} и запустите ./manage.sh doctor"
    elif confirm "Настроить .env интерактивно?" y; then
        cp "$ENV_EXAMPLE" "$ENV_FILE"

        local token=""
        while [[ -z "$token" ]]; do
            if [[ -n "$INPUT_FD" ]]; then
                read -r -s -p "Введите токен бота (у @BotFather) [ввод скрыт]: " token < "$INPUT_FD" || token=""
                echo
            fi
            [[ -n "$token" ]] || warn "Токен не может быть пустым"
        done
        sed -i "s|^BOT_TOKEN=.*|BOT_TOKEN=${token}|" "$ENV_FILE"

        ask "" "Telegram ID ребёнка (CHILD_ID)"
        sed -i "s|^CHILD_ID=.*|CHILD_ID=${REPLY}|" "$ENV_FILE"

        ask "" "Telegram ID родителей через запятую (PARENT_IDS)"
        sed -i "s|^PARENT_IDS=.*|PARENT_IDS=${REPLY}|" "$ENV_FILE"

        ask "5" "Смещение часового пояса от UTC (TZ_OFFSET, Екатеринбург=5)"
        sed -i "s|^TZ_OFFSET=.*|TZ_OFFSET=${REPLY}|" "$ENV_FILE"

        ask "8080" "Порт веб-версии (0 = отключить; 80/443 занимает Caddy)"
        sed -i "s|^WEBAPP_PORT=.*|WEBAPP_PORT=${REPLY}|" "$ENV_FILE"
        if [[ "$REPLY" != "0" ]] && port_in_use "$REPLY"; then
            warn "Порт ${REPLY} уже занят — возможно, другим ботом. Смените WEBAPP_PORT в ${ENV_FILE}"
        fi

        ok ".env создан"
        warn "Проверьте .env: CHILD_NAME, TARGET_PEF при необходимости"
    else
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        warn "Скопирован .env.example -> .env. Отредактируйте его: nano .env"
        info "После настройки запустите: ./manage.sh doctor"
    fi

    chmod 600 "$ENV_FILE" 2>/dev/null || true

    if service_exists; then
        info "systemd-сервис уже установлен"
    elif systemd_available; then
        if confirm "Установить systemd-сервис (автозапуск)?" y; then
            install_service
        else
            info "Хорошо. Запуск вручную: ./manage.sh start"
        fi
    else
        info "systemd недоступен — запуск вручную: ./manage.sh start"
    fi

    echo
    ok "Установка завершена!"
    dim "Дальше: ./manage.sh doctor — проверка конфигурации"
    dim "  Запуск: ./manage.sh start"
    dim "  Логи:   ./manage.sh logs"
}

# --- start / stop / restart / status / logs ----------------------------------
do_stop() {
    if service_active; then
        systemctl stop "${SERVICE_NAME}.service" && ok "Сервис остановлен"
    elif [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        local pid
        pid=$(cat "$PID_FILE")
        kill "$pid" 2>/dev/null || true
        local waited=0
        while kill -0 "$pid" 2>/dev/null && (( waited < 10 )); do
            sleep 1; waited=$((waited + 1))
        done
        if kill -0 "$pid" 2>/dev/null; then
            warn "Процесс ${pid} не завершился за 10с — отправляю SIGKILL"
            kill -9 "$pid" 2>/dev/null || true
            sleep 1
        fi
        rm -f "$PID_FILE"
        ok "Процесс остановлен"
    elif external_pid >/dev/null; then
        local pid
        pid=$(external_pid)
        warn "Найден процесс бота (PID ${pid}), запущенный вне скрипта"
        if confirm "Остановить его?" y; then
            kill "$pid" 2>/dev/null || true
            local waited=0
            while kill -0 "$pid" 2>/dev/null && (( waited < 10 )); do
                sleep 1; waited=$((waited + 1))
            done
            kill -9 "$pid" 2>/dev/null || true
            ok "Процесс остановлен"
        else
            info "Оставляю процесс как есть"
        fi
    else
        info "Бот не запущен"
    fi
}

do_start() {
    if ! token_configured; then
        die "Токен бота не задан в ${ENV_FILE}. Впишите BOT_TOKEN и повторите"
    fi
    if service_exists; then
        systemctl start "${SERVICE_NAME}.service" && ok "Сервис запущен"
    else
        if external_pid >/dev/null; then
            warn "Бот уже запущен (PID $(external_pid)) — не запускаю второй"
            return 0
        fi
        mkdir -p "$LOG_DIR"
        ( cd "$SCRIPT_DIR" && exec nohup "$VENV_DIR/bin/python" bot.py >> "$LOG_DIR/bot.log" 2>&1 ) &
        local bgpid=$!
        echo "$bgpid" > "$PID_FILE"
        ok "Бот запущен вручную (PID ${bgpid})"
    fi
}

cmd_start() {
    do_start
    info "Жду ответа /healthz (до ${HEALTH_TIMEOUT}с)..."
    if health_check "$(get_port)"; then ok "Бот работает"; else warn "Health-check не прошёл — смотрите логи: ./manage.sh logs"; fi
    return 0
}

cmd_stop() { do_stop; }

cmd_restart() {
    do_stop || true
    do_start
    info "Жду ответа /healthz (до ${HEALTH_TIMEOUT}с)..."
    if health_check "$(get_port)"; then ok "Бот работает"; else warn "Health-check не прошёл — смотрите логи: ./manage.sh logs"; fi
    return 0
}

cmd_status() {
    echo "${C_INFO}=== Статус бота ===${C_OFF}"
    if service_exists; then
        if service_active; then
            ok "systemd: активен"
        else
            fail "systemd: неактивен"
        fi
        systemctl status "${SERVICE_NAME}.service" --no-pager -l | tail -n +1 || true
    elif [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        ok "Процесс вручную: PID $(cat "$PID_FILE")"
    elif external_pid >/dev/null; then
        ok "Процесс (внешний): PID $(external_pid)"
    else
        warn "Бот не запущен"
    fi

    local port
    port=$(get_port)
    if [[ "$port" == "0" ]]; then
        dim "Веб-сервер отключён (WEBAPP_PORT=0)"
    elif curl -sf "http://localhost:${port}/healthz" >/dev/null 2>&1; then
        ok "Веб /healthz: отвечает (порт ${port})"
    else
        warn "Веб /healthz: нет ответа (порт ${port})"
    fi
}

cmd_logs() {
    if service_exists && service_active; then
        journalctl -u "${SERVICE_NAME}.service" -f --no-pager -n 50
    elif [[ -f "${LOG_DIR}/bot.log" ]]; then
        tail -f "${LOG_DIR}/bot.log"
    else
        die "Логов ещё нет"
    fi
}

# --- update ------------------------------------------------------------------
cmd_update() {
    info "Обновление бота из GitHub"

    if ! git -C "$SCRIPT_DIR" diff --quiet 2>/dev/null || ! git -C "$SCRIPT_DIR" diff --cached --quiet 2>/dev/null; then
        die "Локальные изменения в git — закоммитьте или сделайте git stash перед обновлением"
    fi
    local untracked
    untracked=$(git -C "$SCRIPT_DIR" ls-files --others --exclude-standard | grep -vE '^(logs|backups)/' || true)
    if [[ -n "$untracked" ]]; then
        warn "Незакоммиченные файлы (обновятся только отслеживаемые):"
        dim "$untracked"
        confirm "Продолжить?" n || exit 1
    fi

    info "Получаю изменения..."
    git -C "$SCRIPT_DIR" fetch origin || die "git fetch не удался — проверьте сеть"
    local current ahead remote_branch
    current=$(git -C "$SCRIPT_DIR" rev-parse HEAD)
    remote_branch=$(git -C "$SCRIPT_DIR" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || echo "origin/main")
    ahead=$(git -C "$SCRIPT_DIR" rev-list --count "${current}..${remote_branch}" 2>/dev/null || echo "?")
    if [[ "$ahead" == "0" ]]; then
        ok "Уже актуальная версия"
        exit 0
    fi
    dim "Доступно новых коммитов: ${ahead}"

    cmd_backup

    info "Загружаю новую версию..."
    git -C "$SCRIPT_DIR" pull --ff-only origin || die "git pull не удался. Бэкап в ${BACKUP_DIR}"

    if [[ -d "$VENV_DIR" ]]; then
        "$VENV_DIR/bin/pip" install --quiet -r "${SCRIPT_DIR}/requirements.txt" \
            || warn "Не удалось обновить зависимости — проверьте вручную"
    fi

    local was_running=false
    service_running && was_running=true
    if [[ "$was_running" != "true" ]]; then
        info "Бот не был запущен — только обновляю код, без запуска"
        git -C "$SCRIPT_DIR" log --oneline "${current}..HEAD" | head -20
        ok "Обновление завершено"
        return 0
    fi
    do_stop || true
    do_start

    info "Жду ответа /healthz (до ${HEALTH_TIMEOUT}с)..."
    if health_check "$(get_port)"; then
        echo
        ok "Обновление успешно! Новые коммиты:"
        git -C "$SCRIPT_DIR" log --oneline "${current}..HEAD" | head -20
        return 0
    fi

    echo
    fail "Бот не поднялся после обновления — откатываюсь"
    do_stop || true
    git -C "$SCRIPT_DIR" reset --hard "$current" || die "Не удалось откатить git!"
    warn "Откат к коммиту $(git -C "$SCRIPT_DIR" rev-parse --short "$current")"
    do_start || true
    if health_check "$(get_port)"; then
        warn "Откат успешен, бот работает на старой версии"
    else
        fail "Бот не поднялся даже после отката — смотрите логи: ./manage.sh logs"
    fi
    exit 1
}

# --- backup / restore ---------------------------------------------------------
cmd_backup() {
    mkdir -p "$BACKUP_DIR"
    local stamp target db_file
    stamp=$(date +%Y%m%d-%H%M%S)
    target="${BACKUP_DIR}/bot-backup-${stamp}.tar.gz"
    db_file=$(get_db_file)

    local staging
    staging=$(mktemp -d "${BACKUP_DIR}/.staging-${stamp}.XXXXXX") || die "Не удалось создать временный каталог"
    local db_name
    db_name=$(basename "$db_file")

    if [[ -f "$db_file" ]]; then
        local have_db=false
        if command -v sqlite3 >/dev/null 2>&1; then
            if sqlite3 "$db_file" ".backup '${staging}/${db_name}'"; then
                have_db=true
            else
                warn "sqlite3 .backup не удался — останавливаю бота и копирую файл напрямую"
                rm -f "${staging}/${db_name}"
            fi
        fi
        if [[ "$have_db" != "true" ]]; then
            local was_running=false
            service_running && was_running=true
            do_stop || true
            if ! cp -f "$db_file" "${staging}/${db_name}"; then
                warn "Не удалось скопировать БД"
                [[ "$was_running" == "true" ]] && do_start || true
                rm -rf "$staging"; rm -f "$target"
                return 1
            fi
            [[ -f "${db_file}-wal" ]] && cp -f "${db_file}-wal" "${staging}/${db_name}-wal" 2>/dev/null || true
            [[ -f "${db_file}-shm" ]] && cp -f "${db_file}-shm" "${staging}/${db_name}-shm" 2>/dev/null || true
            if [[ "$was_running" == "true" ]]; then
                do_start || true
            fi
        fi
    fi

    [[ -f "$ENV_FILE" ]] && cp -f "$ENV_FILE" "${staging}/.env"

    if ! find "$staging" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
        warn "Нет данных для бэкапа (БД и .env отсутствуют)"
        rm -rf "$staging"
        return 0
    fi

    tar -czf "$target" -C "$staging" . \
        || { rm -rf "$staging"; rm -f "$target"; die "Не удалось создать бэкап"; }
    rm -rf "$staging"
    chmod 600 "$target" 2>/dev/null || true
    ok "Бэкап: ${target} ($(du -h "$target" | cut -f1))"

    local old
    old=$(find "$BACKUP_DIR" -name 'bot-backup-*.tar.gz' -printf '%T@ %p\n' 2>/dev/null | sort -rn | tail -n +11 | cut -d' ' -f2- || true)
    if [[ -n "$old" ]]; then
        dim "Удаляю старые бэкапы: $(wc -l <<< "$old") шт."
        while IFS= read -r f; do rm -f "$f"; done <<< "$old"
    fi
}

cmd_restore() {
    local latest
    latest=$(find "$BACKUP_DIR" -name 'bot-backup-*.tar.gz' -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2- || true)
    [[ -n "$latest" ]] || die "Бэкапов нет в ${BACKUP_DIR}"

    confirm "Восстановить ${latest}? Бот будет остановлен." n || exit 0
    do_stop || true

    local db_file db_name tmp
    db_file=$(get_db_file)
    db_name=$(basename "$db_file")
    tmp=$(mktemp -d "${BACKUP_DIR}/.restore-XXXXXX") || die "Не удалось создать временный каталог"
    tar -xzf "$latest" -C "$tmp" || { rm -rf "$tmp"; die "Не удалось распаковать бэкап"; }

    if [[ -f "${tmp}/${db_name}" ]]; then
        mkdir -p "$(dirname "$db_file")"
        rm -f "${db_file}-wal" "${db_file}-shm"
        cp -f "${tmp}/${db_name}" "$db_file" || { rm -rf "$tmp"; die "Не удалось восстановить БД"; }
        [[ -f "${tmp}/${db_name}-wal" ]] && cp -f "${tmp}/${db_name}-wal" "${db_file}-wal" || true
        [[ -f "${tmp}/${db_name}-shm" ]] && cp -f "${tmp}/${db_name}-shm" "${db_file}-shm" || true
    fi
    [[ -f "${tmp}/.env" ]] && cp -f "${tmp}/.env" "$ENV_FILE" || true
    rm -rf "$tmp"

    ok "Восстановлено из $(basename "$latest")"
    do_start
    if health_check "$(get_port)"; then ok "Бот работает"; else warn "Проверьте логи: ./manage.sh logs"; fi
    return 0
}

# --- doctor -------------------------------------------------------------------
cmd_doctor() {
    echo "${C_INFO}=== Диагностика ===${C_OFF}"
    local errors=0

    if [[ -d "$VENV_DIR" ]] && [[ -x "$VENV_DIR/bin/python" ]]; then
        ok "venv: $("$VENV_DIR"/bin/python --version 2>&1)"
    else
        fail "venv не найден — запустите: ./manage.sh install"
        errors=$((errors + 1))
    fi

    if [[ -x "$VENV_DIR/bin/python" ]] && "$VENV_DIR/bin/python" -c 'import aiogram, matplotlib, fastapi, uvicorn' 2>/dev/null; then
        ok "Зависимости: установлены"
    else
        fail "Зависимости не установлены — запустите: ./manage.sh install"
        errors=$((errors + 1))
    fi

    if [[ -f "$ENV_FILE" ]]; then
        ok ".env: найден"
        local token child parents
        token=$(env_get BOT_TOKEN)
        child=$(env_get CHILD_ID)
        parents=$(env_get PARENT_IDS)
        if [[ -z "$token" ]]; then
            fail "BOT_TOKEN: не задан"
            errors=$((errors + 1))
        else
            ok "BOT_TOKEN: задан"
        fi
        if [[ -z "$child" || "$child" == "0" ]]; then
            fail "CHILD_ID: не задан"
            errors=$((errors + 1))
        else
            ok "CHILD_ID: задан"
        fi
        if [[ -z "$parents" || "$parents" == "0,0" ]]; then
            fail "PARENT_IDS: не заданы"
            errors=$((errors + 1))
        else
            ok "PARENT_IDS: заданы"
        fi
    else
        fail ".env не найден — cp .env.example .env"
        errors=$((errors + 1))
    fi

    local dir
    for dir in logs backups; do
        if [[ -d "${SCRIPT_DIR}/${dir}" ]]; then
            ok "Директория ${dir}/: есть"
        else
            warn "Директория ${dir}/: нет (создастся при install/запуске)"
        fi
    done

    if service_active; then
        ok "systemd-сервис: активен"
    elif [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        ok "Процесс: PID $(cat "$PID_FILE")"
    elif external_pid >/dev/null; then
        ok "Процесс (внешний): PID $(external_pid)"
    else
        warn "Бот не запущен — ./manage.sh start"
    fi

    local port
    port=$(get_port)
    if [[ "$port" == "0" ]]; then
        dim "Веб-сервер отключён (WEBAPP_PORT=0)"
    elif curl -sf "http://localhost:${port}/healthz" >/dev/null 2>&1; then
        ok "Веб /healthz: отвечает (порт ${port})"
    else
        warn "Веб /healthz: нет ответа (порт ${port})"
    fi

    local domain
    domain=$(get_webapp_domain)
    if [[ -n "$domain" ]]; then
        if curl -sf "https://${domain}/healthz" >/dev/null 2>&1; then
            ok "Mini App HTTPS: https://${domain}/healthz отвечает"
        elif command -v caddy >/dev/null 2>&1; then
            warn "Mini App HTTPS: нет ответа — проверьте: ./manage.sh caddy"
        else
            warn "Mini App HTTPS: Caddy не установлен — запустите: ./manage.sh caddy"
        fi
    fi

    echo
    if (( errors > 0 )); then
        fail "Проблем: ${errors}. Исправьте и повторите: ./manage.sh doctor"
        exit 1
    else
        ok "Все проверки пройдены"
    fi
}

# --- caddy (TLS reverse proxy) -------------------------------------------------
install_caddy() {
    command -v caddy >/dev/null 2>&1 && return 0
    info "Устанавливаю Caddy..."
    local pm
    pm=$(system_pm) || die "Пакетный менеджер не найден — установите Caddy вручную: https://caddyserver.com/docs/install"
    case "$pm" in
        apt-get)
            install_pkgs debian-keyring debian-archive-keyring apt-transport-https curl
            curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
                | run_root gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
            curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
                | run_root tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
            run_root apt-get update -qq
            run_root apt-get install -y -qq caddy
            ;;
        dnf)
            run_root dnf install -y -q 'dnf-command(copr)'
            run_root dnf copr enable -y @caddy/caddy
            run_root dnf install -y -q caddy
            ;;
        yum)
            run_root yum install -y -q yum-plugin-copr
            run_root yum copr enable -y @caddy/caddy
            run_root yum install -y -q caddy
            ;;
        apk)
            run_root apk add --quiet caddy
            ;;
    esac
    command -v caddy >/dev/null 2>&1 || die "Caddy не установился — поставьте вручную: https://caddyserver.com/docs/install"
    ok "Caddy установлен: $(caddy version 2>/dev/null | head -1)"
}

port_in_use() {
    # True если TCP-порт уже кем-то слушается (Caddy занимает 80/443 — это норма,
    # поэтому проверяем только порт самого бота).
    local port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -H -ltn "sport = :${port}" 2>/dev/null | grep -q . && return 0
    elif command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1 && return 0
    fi
    return 1
}

ensure_main_caddyfile() {
    # Базовый /etc/caddy/Caddyfile держит только импорт фрагментов ботов.
    # Создаём, если нет; дописываем import, если отсутствует; чужое не затираем.
    run_root mkdir -p "${CADDY_DIR}/conf.d"
    if [[ ! -f "$CADDY_MAIN" ]]; then
        printf '# Управляется manage.sh: базовый конфиг + фрагменты ботов в conf.d/\nimport %s/conf.d/*.caddy\n' "$CADDY_DIR" \
            | run_root tee "$CADDY_MAIN" >/dev/null
        ok "Создан ${CADDY_MAIN} (импорт conf.d/*.caddy)"
    elif ! grep -qE "^import[[:space:]]+${CADDY_DIR}/conf\.d/\*\.caddy" "$CADDY_MAIN" 2>/dev/null; then
        printf '\n# Добавлено manage.sh: фрагменты ботов\nimport %s/conf.d/*.caddy\n' "$CADDY_DIR" \
            | run_root tee -a "$CADDY_MAIN" >/dev/null
        warn "В ${CADDY_MAIN} добавлен import conf.d/*.caddy (существующий конфиг сохранён)"
    fi
    run_root mkdir -p "$(dirname "$CADDY_SITE")"
}

cmd_caddy() {
    local domain port
    domain=$(get_webapp_domain)
    port=$(get_port)

    if [[ -z "$domain" ]]; then
        die "WEBAPP_URL не задан в ${ENV_FILE}. Укажите публичный HTTPS-URL, например:
  WEBAPP_URL=https://pick.example.com
Затем повторите: ./manage.sh caddy"
    fi
    if [[ "$port" == "0" ]]; then
        die "WEBAPP_PORT=0 — веб-сервер бота отключён, Caddy проксировать некуда"
    fi
    if [[ "$port" == "80" || "$port" == "443" ]]; then
        die "WEBAPP_PORT=${port} — этот порт нужен Caddy для HTTPS и редиректов.
Смените порт бота (например, 8080) в ${ENV_FILE} и перезапустите: ./manage.sh restart"
    fi

    info "Настройка Caddy для ${INSTANCE}: ${domain} -> 127.0.0.1:${port}"
    info "Для выпуска сертификата Let's Encrypt нужны открытые порты 80 и 443 и DNS-запись на этот сервер."

    install_caddy
    ensure_main_caddyfile

    # Свой фрагмент — другие боты пишут свои, не конфликтуя.
    printf '%s {\n\treverse_proxy 127.0.0.1:%s\n}\n' "$domain" "$port" \
        | run_root tee "$CADDY_SITE" >/dev/null
    ok "Фрагмент: ${CADDY_SITE} (${domain} -> 127.0.0.1:${port})"

    run_root caddy validate --config "$CADDY_MAIN" --adapter caddyfile \
        || die "Конфиг Caddy некорректен — проверьте ${CADDY_MAIN} и ${CADDY_SITE}"

    if systemd_available; then
        run_root systemctl enable --now caddy >/dev/null 2>&1 || true
        run_root systemctl reload caddy >/dev/null 2>&1 || run_root systemctl restart caddy
        ok "Caddy перезапущен (systemd)"
    else
        warn "systemd не найден — запустите Caddy вручную: caddy run --config ${CADDY_MAIN}"
    fi

    info "Жду ответа https://${domain}/healthz (до ${HEALTH_TIMEOUT}с)..."
    local waited=0
    while (( waited < HEALTH_TIMEOUT )); do
        if curl -sf "https://${domain}/healthz" >/dev/null 2>&1; then
            echo
            ok "HTTPS работает: https://${domain}/healthz"
            dim "Проверьте WEBAPP_URL в .env: должен быть https://${domain}"
            return 0
        fi
        sleep 2; waited=$((waited + 2))
    done
    echo
    warn "HTTPS не ответил за ${HEALTH_TIMEOUT}с. Проверьте:"
    dim "  • DNS ${domain} указывает на IP этого сервера"
    dim "  • порты 80/443 открыты (firewall)"
    dim "  • фрагмент бота: ${CADDY_SITE}"
    dim "  • логи Caddy: journalctl -u caddy -n 50 --no-pager"
    return 1
}

# --- uninstall -----------------------------------------------------------------
cmd_uninstall() {
    echo "${C_WARN}Внимание: это остановит бота${C_OFF}"
    confirm "Продолжить удаление?" n || exit 0

    do_stop || true
    if service_exists; then
        systemctl disable "${SERVICE_NAME}.service" >/dev/null 2>&1 || true
        rm -f "$SERVICE_FILE" && systemctl daemon-reload
        ok "systemd-сервис удалён"
    fi

    if [[ -f "$CADDY_SITE" ]]; then
        if confirm "Удалить Caddy-фрагмент ${CADDY_SITE} для ${INSTANCE}?" y; then
            run_root rm -f "$CADDY_SITE"
            if systemd_available && command -v caddy >/dev/null 2>&1; then
                run_root systemctl reload caddy >/dev/null 2>&1 || true
            fi
            ok "Caddy-фрагмент удалён (${INSTANCE})"
        else
            info "Caddy-фрагмент оставлен: ${CADDY_SITE}"
        fi
    fi

    if confirm "Удалить виртуальное окружение (.venv)?" n; then
        rm -rf "$VENV_DIR" && ok ".venv удалён"
    fi

    if confirm "Удалить данные и бэкапы (БД, backups/, .env)?" n; then
        local db_file
        db_file=$(get_db_file)
        rm -f "$db_file" "${db_file}-wal" "${db_file}-shm" "$ENV_FILE"
        rm -rf "$BACKUP_DIR"
        ok "Данные удалены"
    else
        info "Данные сохранены: БД, backups/, .env"
    fi
    ok "Удаление завершено. Код бота в ${SCRIPT_DIR} не тронут."
}

# --- usage ---------------------------------------------------------------------
cmd_help() {
    cat <<EOF
Управление Telegram-ботом пикфлоуметрии

Использование: ./manage.sh [--instance ИМЯ] <команда>

Быстрая установка с нуля (клонирует в ${INSTALL_DIR_DEFAULT}):
  curl -fsSL https://raw.githubusercontent.com/itdevlog/peakflow/main/manage.sh | bash -s -- install

Несколько ботов на одном сервере:
  Каждый бот — отдельный каталог со своим .env. Имя инстанса берётся из имени
  каталога (или флагами --instance / RASPISANIE_INSTANCE) и разводит systemd-юнит
  (${LEGACY_SERVICE_NAME}-<instance>), PID-файл и фрагмент Caddy
  (/etc/caddy/conf.d/<instance>.caddy). Caddy ставится один, слушает 80/443,
  а боты — на уникальных портах (8080, 8081, ...) и WEBAPP_HOST=127.0.0.1.

Команды:
  install     Полная установка: venv, зависимости, .env, systemd (интерактивно).
              Без репозитория рядом (curl|bash) — клонирует в ${INSTALL_DIR_DEFAULT}
  update      Обновление с GitHub + бэкап + откат при сбое
  start       Запуск бота
  stop        Остановка
  restart     Перезапуск
  status      Статус сервиса + health-check
  logs        Логи в реальном времени (Ctrl+C для выхода)
  backup      Бэкап БД + .env в backups/ (хранит последние 10)
  restore     Восстановление из последнего бэкапа
  doctor      Диагностика: venv, зависимости, .env, сервис, /healthz
  caddy       HTTPS для Mini App: ставит Caddy, берёт домен из WEBAPP_URL
  uninstall   Остановка + удаление сервиса и Caddy-фрагмента (с вопросами)
  help        Эта справка

Флаги и переменные:
  --instance ИМЯ        Имя инстанса (по умолчанию — имя каталога)
  RASPISANIE_INSTANCE   То же через переменную окружения
  --no-color            Отключить цвета
EOF
}

# --- bootstrap (curl | bash) ----------------------------------------------------
cmd_bootstrap_install() {
    info "Скрипт запущен вне репозитория — устанавливаю с GitHub"

    local install_dir="$INSTALL_DIR_DEFAULT"
    local reply=""
    if [[ -n "$INPUT_FD" ]]; then
        read -r -p "Каталог установки [${INSTALL_DIR_DEFAULT}]: " reply < "$INPUT_FD" || reply=""
    fi
    reply="${reply:-$INSTALL_DIR_DEFAULT}"
    install_dir="$reply"

    if [[ -d "$install_dir/.git" ]]; then
        info "Каталог ${install_dir} уже содержит репозиторий — обновляю код"
        ensure_cmd git git
        git -C "$install_dir" fetch origin 2>/dev/null || die "Не удалось обновить ${install_dir} из GitHub"
        git -C "$install_dir" reset --hard "$(git -C "$install_dir" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || echo origin/main)" >/dev/null
    elif [[ -e "$install_dir" ]]; then
        die "Каталог ${install_dir} занят — выберите другой путь установки"
    else
        info "Клонирую ${REPO_URL} -> ${install_dir}"
        ensure_cmd git git
        mkdir -p "$(dirname "$install_dir")"
        git clone "$REPO_URL" "$install_dir" || die "Не удалось клонировать репозиторий"
    fi

    if [[ -n "${NO_COLOR:-}" ]]; then
        exec bash "$install_dir/manage.sh" --no-color install
    else
        exec bash "$install_dir/manage.sh" install
    fi
}

# --- main ----------------------------------------------------------------------
main() {
    # --no-color распознаётся в любой позиции; --instance ИМЯ — имя инстанса.
    local args=() no_color=false instance="" arg
    while [[ $# -gt 0 ]]; do
        arg="$1"
        if [[ "$arg" == "--no-color" ]]; then
            no_color=true
            shift
        elif [[ "$arg" == "--instance" ]]; then
            [[ -n "${2:-}" ]] || die "--instance требует имя"
            instance="$2"
            shift 2
        elif [[ "$arg" == --instance=* ]]; then
            instance="${arg#*=}"
            shift
        else
            args+=("$arg")
            shift
        fi
    done
    if [[ "$no_color" == "true" ]]; then
        set_colors off
        export NO_COLOR=1
    fi
    [[ -n "$instance" ]] && init_instance "$instance"
    if [[ ${#args[@]} -gt 0 ]]; then
        set -- "${args[@]}"
    else
        set --
    fi
    local cmd="${1:-help}"
    shift 2>/dev/null || true

    if ! is_repo; then
        case "$cmd" in
            install) cmd_bootstrap_install ;;
            help|-h|--help|"") cmd_help ;;
            *) die "Эта команда работает только внутри установленного репозитория.
Быстрая установка с нуля: curl -fsSL https://raw.githubusercontent.com/itdevlog/peakflow/main/manage.sh | bash -s -- install" ;;
        esac
        return
    fi

    case "$cmd" in
        install)   cmd_install ;;
        update)    cmd_update ;;
        start)     cmd_start ;;
        stop)      cmd_stop ;;
        restart)   cmd_restart ;;
        status)    cmd_status ;;
        logs)      cmd_logs ;;
        backup)    cmd_backup ;;
        restore)   cmd_restore ;;
        doctor)    cmd_doctor ;;
        caddy)     cmd_caddy ;;
        uninstall) cmd_uninstall ;;
        help|-h|--help|"") cmd_help ;;
        *) die "Неизвестная команда: '${cmd}'. Смотрите: ./manage.sh help" ;;
    esac
}

main "$@"
