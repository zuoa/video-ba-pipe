#!/bin/bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_BACKEND="${DB_BACKEND:-}"
DB_PATH="${DB_PATH:-${ROOT_DIR}/app/data/db/ba.db}"
if [ -z "${DB_BACKEND}" ]; then
    if [ -n "${DB_HOST:-}" ] || [ -n "${DB_PORT:-}" ] || [ -n "${DB_NAME:-}" ] || [ -n "${DB_USER:-}" ] || [ -n "${DB_PASSWORD:-}" ]; then
        DB_BACKEND="postgres"
    else
        DB_BACKEND="sqlite"
    fi
fi
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-video_ba_pipe}"
DB_USER="${DB_USER:-video_ba_pipe}"
DB_PASSWORD="${DB_PASSWORD:-video_ba_pipe}"
DB_SSLMODE="${DB_SSLMODE:-prefer}"
LOG_DIR="${LOG_DIR:-${ROOT_DIR}/app/data/logs}"
WINDOW_MINUTES="${1:-10}"
SOURCE_ID_FILTER="${2:-}"
TAIL_LINES="${TAIL_LINES:-5000}"

if ! [[ "${WINDOW_MINUTES}" =~ ^[0-9]+$ ]] || [ "${WINDOW_MINUTES}" -le 0 ]; then
    echo "Usage: scripts/check_health.sh [minutes] [source_id]"
    echo "Example: scripts/check_health.sh 10"
    echo "Example: scripts/check_health.sh 30 2"
    exit 1
fi

if ! command -v rg >/dev/null 2>&1; then
    echo "rg is required but not found in PATH"
    exit 1
fi

db_query() {
    if [ "${DB_BACKEND}" = "sqlite" ]; then
        sqlite3 -tabs "${DB_PATH}" "$1"
    else
        psql -v ON_ERROR_STOP=1 -t -A -F $'\t' -c "$1"
    fi
}

if [ "${DB_BACKEND}" = "sqlite" ]; then
    if [ ! -f "${DB_PATH}" ]; then
        echo "Database not found: ${DB_PATH}"
        exit 1
    fi
    if ! command -v sqlite3 >/dev/null 2>&1; then
        echo "sqlite3 is required but not found in PATH"
        exit 1
    fi
else
    if ! command -v psql >/dev/null 2>&1; then
        echo "psql is required but not found in PATH"
        exit 1
    fi
    export PGHOST="${DB_HOST}"
    export PGPORT="${DB_PORT}"
    export PGDATABASE="${DB_NAME}"
    export PGUSER="${DB_USER}"
    export PGPASSWORD="${DB_PASSWORD}"
    export PGSSLMODE="${DB_SSLMODE}"
fi

SQL_SOURCE_FILTER=""
if [ -n "${SOURCE_ID_FILTER}" ]; then
    if ! [[ "${SOURCE_ID_FILTER}" =~ ^[0-9]+$ ]]; then
        echo "source_id must be an integer"
        exit 1
    fi
    SQL_SOURCE_FILTER=" and v.id = ${SOURCE_ID_FILTER}"
fi

if [ "${DB_BACKEND}" = "sqlite" ]; then
    RECENT_WHERE="l.created_at >= datetime('now', 'localtime', '-${WINDOW_MINUTES} minutes')"
else
    RECENT_WHERE="l.created_at >= now() - interval '${WINDOW_MINUTES} minutes'"
fi
if [ -n "${SOURCE_ID_FILTER}" ]; then
    RECENT_WHERE="${RECENT_WHERE} and l.source_id = ${SOURCE_ID_FILTER}"
fi

print_header() {
    echo
    echo "============================================================"
    echo "$1"
    echo "============================================================"
}

print_header "Health Check Summary"
echo "project_root: ${ROOT_DIR}"
echo "db_backend: ${DB_BACKEND}"
if [ "${DB_BACKEND}" = "sqlite" ]; then
    echo "db_path: ${DB_PATH}"
else
    echo "db_host: ${DB_HOST}"
    echo "db_port: ${DB_PORT}"
    echo "db_name: ${DB_NAME}"
    echo "db_user: ${DB_USER}"
fi
echo "log_dir: ${LOG_DIR}"
echo "window_minutes: ${WINDOW_MINUTES}"
if [ -n "${SOURCE_ID_FILTER}" ]; then
    echo "source_id: ${SOURCE_ID_FILTER}"
else
    echo "source_id: all"
fi

print_header "Current Source Status"

STATUS_QUERY="
select
  v.id,
  v.name,
  v.status,
  coalesce(v.decoder_pid, ''),
  v.source_decode_width || 'x' || v.source_decode_height || '@' || v.source_fps,
  coalesce((
    select l.event_type
    from source_health_logs l
    where l.source_id = v.id
    order by l.created_at desc
    limit 1
  ), ''),
  coalesce((
    select l.created_at
    from source_health_logs l
    where l.source_id = v.id
    order by l.created_at desc
    limit 1
  ), '')
from videosource v
where 1=1 ${SQL_SOURCE_FILTER}
order by v.id;
"

HAS_STATUS_ROWS=0
INCONSISTENT_COUNT=0
while IFS=$'\t' read -r source_id source_name source_status decoder_pid source_profile last_event last_event_at; do
    HAS_STATUS_ROWS=1
    pid_state="n/a"
    if [ -n "${decoder_pid}" ]; then
        if kill -0 "${decoder_pid}" 2>/dev/null; then
            pid_state="alive"
        else
            pid_state="dead"
        fi
    fi

    echo "[${source_id}] ${source_name}"
    echo "  status: ${source_status}"
    echo "  decoder_pid: ${decoder_pid:-none} (${pid_state})"
    echo "  profile: ${source_profile}"
    echo "  last_event: ${last_event:-none}"
    echo "  last_event_at: ${last_event_at:-none}"

    if [ "${source_status}" = "RUNNING" ] && [ -n "${decoder_pid}" ] && [ "${pid_state}" = "dead" ]; then
        echo "  warning: status says RUNNING but decoder_pid is not alive"
        INCONSISTENT_COUNT=$((INCONSISTENT_COUNT + 1))
    fi
    if [ "${source_status}" = "RUNNING" ] && [ -z "${decoder_pid}" ]; then
        echo "  warning: status says RUNNING but decoder_pid is empty"
        INCONSISTENT_COUNT=$((INCONSISTENT_COUNT + 1))
    fi
done < <(db_query "${STATUS_QUERY}")

if [ "${HAS_STATUS_ROWS}" -eq 0 ]; then
    echo "No matching video sources found."
elif [ "${INCONSISTENT_COUNT}" -gt 0 ]; then
    echo
    echo "State anomalies detected: ${INCONSISTENT_COUNT}"
fi

print_header "Recent Health Events"

EVENT_SUMMARY_QUERY="
select
  v.id,
  v.name,
  l.event_type,
  l.severity,
  count(*) as event_count,
  max(l.created_at) as last_seen_at
from source_health_logs l
join videosource v on v.id = l.source_id
where ${RECENT_WHERE}
group by v.id, v.name, l.event_type, l.severity
order by last_seen_at desc, event_count desc;
"

EVENT_ROWS="$(db_query "${EVENT_SUMMARY_QUERY}")"
if [ -n "${EVENT_ROWS}" ]; then
    while IFS=$'\t' read -r source_id source_name event_type severity event_count last_seen_at; do
        echo "[${source_id}] ${source_name} | ${event_type} | ${severity} | count=${event_count} | last_seen=${last_seen_at}"
    done <<< "${EVENT_ROWS}"
else
    echo "No health events in the last ${WINDOW_MINUTES} minute(s)."
fi

print_header "Recent Process Exit Details"

PROCESS_EXIT_QUERY="
select
  l.created_at,
  v.id,
  v.name,
  l.details
from source_health_logs l
join videosource v on v.id = l.source_id
where ${RECENT_WHERE}
  and l.event_type = 'process_exit'
order by l.created_at desc
limit 20;
"

PROCESS_EXIT_ROWS="$(db_query "${PROCESS_EXIT_QUERY}")"
if [ -n "${PROCESS_EXIT_ROWS}" ]; then
    while IFS=$'\t' read -r created_at source_id source_name details; do
        echo "${created_at} | [${source_id}] ${source_name} | ${details}"
    done <<< "${PROCESS_EXIT_ROWS}"
else
    echo "No process_exit events in the last ${WINDOW_MINUTES} minute(s)."
fi

print_header "Recent Relevant Log Lines"

LOG_PATTERNS='process_exit|已退出|异常退出|启动失败|连续 .* 次获取帧失败|错误过多|无有效帧输出|视频流已停止|后台执行失败|单帧执行失败|Source host .*已退出'
LOG_FILES=()

for candidate in "${LOG_DIR}/run.log" "${LOG_DIR}/debug.log" "${LOG_DIR}/workflow.log" "${LOG_DIR}/workflow_debug.log"; do
    if [ -f "${candidate}" ]; then
        LOG_FILES+=("${candidate}")
    fi
done

if [ "${#LOG_FILES[@]}" -eq 0 ]; then
    echo "No log files found under ${LOG_DIR}"
else
    for log_file in "${LOG_FILES[@]}"; do
        echo "--- $(basename "${log_file}") ---"
        if ! tail -n "${TAIL_LINES}" "${log_file}" | rg -n "${LOG_PATTERNS}"; then
            echo "No recent matching lines in $(basename "${log_file}")"
        fi
    done
fi
