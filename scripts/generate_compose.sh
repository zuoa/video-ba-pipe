#!/usr/bin/env bash

set -Eeuo pipefail

RUN_DIR="$(pwd)"
SCRIPT_SOURCE="${BASH_SOURCE[0]:-}"
PROJECT_DIR="${RUN_DIR}"
if [[ -n "${SCRIPT_SOURCE}" ]]; then
  SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${SCRIPT_SOURCE}")" 2>/dev/null && pwd || true)"
  if [[ -n "${SCRIPT_DIR}" &&
        -f "${SCRIPT_DIR}/../scripts/generate_compose.sh" &&
        -f "${SCRIPT_DIR}/../deploy/compose/templates/cpu.yml" &&
        -f "${SCRIPT_DIR}/../deploy/compose/templates/cuda.yml" ]]; then
    PROJECT_DIR="$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)"
  fi
fi

PLATFORM="auto"
PLATFORM_SET=false
ENABLE_MQTT=false
MQTT_SET=false
ENABLE_RABBITMQ=false
RABBITMQ_SET=false
ENABLE_MEDIAMTX=false
MEDIAMTX_SET=false
USE_NJU_MIRROR=true
NJU_MIRROR_SET=false
OUTPUT_FILE="${PROJECT_DIR}/docker-compose.yml"
FORCE=false
INTERACTIVE_MODE="auto"
INTERACTIVE_INPUT_FD=0
DOWNLOAD_CONFIGS=true
FORCE_CONFIGS=false
CONFIG_BASE_URL="${VIDEO_BA_PIPE_CONFIG_BASE_URL:-https://raw.githubusercontent.com/zuoa/video-ba-pipe/main}"
GH_PROXY_BASE_URL="${VIDEO_BA_PIPE_GH_PROXY_BASE_URL-https://gh-proxy.com}"
GENERATE_ENV="auto"
ENV_FILE=""
FORCE_ENV=false

usage() {
  cat <<'EOF'
生成适合目标平台的 Docker Compose 配置。

用法：
  ./scripts/generate_compose.sh [选项]

选项：
  -p, --platform PLATFORM  auto、cpu、cuda、jetson 或 rknn（默认：auto）
  -o, --output FILE        输出文件（默认：docker-compose.yml；- 表示标准输出）
      --with-mqtt          包含内置 MQTT Broker
      --without-mqtt       不包含内置 MQTT Broker（默认）
      --with-rabbitmq      包含内置 RabbitMQ
      --without-rabbitmq   不包含内置 RabbitMQ（默认）
      --with-mediamtx      包含 MediaMTX 实时预览中继
      --without-mediamtx   不包含 MediaMTX（默认）
      --nju-mirror         将 GHCR 替换为南京大学镜像（默认）
      --no-nju-mirror      使用上游 GHCR 地址
      --download-configs   自动准备 Compose 引用的配置文件（默认）
      --no-download-configs
                           不下载或复制配置文件，也不创建 data 目录
      --force-configs      覆盖已有配置文件
      --config-base-url URL
                           配置下载地址（默认：GitHub main 分支）
      --env-file FILE      通过问答生成指定的 .env 文件
      --no-env-file        不生成 .env 文件
      --force-env          覆盖已有 .env 文件
      --interactive        强制启用交互选择
      --non-interactive    禁用交互，使用参数和默认值
  -f, --force              覆盖已存在的输出文件
  -h, --help               显示帮助

示例：
  ./scripts/generate_compose.sh
  ./scripts/generate_compose.sh --non-interactive --platform auto --with-mqtt
  ./scripts/generate_compose.sh -p rknn --with-mediamtx --nju-mirror -o docker-compose.yml
  curl -fsSLo generate_compose.sh https://raw.githubusercontent.com/zuoa/video-ba-pipe/main/scripts/generate_compose.sh && bash generate_compose.sh
EOF
}

die() {
  printf '错误：%s\n' "$*" >&2
  exit 1
}

warn() {
  printf '警告：%s\n' "$*" >&2
}

detect_platform() {
  local machine model compatible
  machine="$(uname -m 2>/dev/null || printf 'unknown')"

  case "${machine}" in
    x86_64|amd64)
      if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
        printf 'cuda\n'
      else
        printf 'cpu\n'
      fi
      ;;
    aarch64|arm64)
      model=""
      compatible=""
      if [[ -r /proc/device-tree/model ]]; then
        model="$(tr -d '\000' </proc/device-tree/model 2>/dev/null || true)"
      fi
      if [[ -r /proc/device-tree/compatible ]]; then
        compatible="$(tr '\000' ' ' </proc/device-tree/compatible 2>/dev/null || true)"
      fi

      if [[ -f /etc/nv_tegra_release ]] ||
         [[ "${model} ${compatible}" =~ [Nn][Vv][Ii][Dd][Ii][Aa]|[Tt][Ee][Gg][Rr][Aa]|[Jj][Ee][Tt][Ss][Oo][Nn] ]]; then
        printf 'jetson\n'
      elif [[ -e /dev/rknpu ]] || [[ -e /dev/rknpu0 ]] ||
           [[ "${model} ${compatible}" =~ [Rr][Oo][Cc][Kk][Cc][Hh][Ii][Pp]|[Rr][Kk]3588 ]]; then
        printf 'rknn\n'
      else
        warn "未识别 ARM64 板卡型号，将使用 CPU 模板；可通过 --platform 手动指定"
        printf 'cpu\n'
      fi
      ;;
    *)
      warn "未识别主机架构 ${machine}，将使用 CPU 模板；可通过 --platform 手动指定"
      printf 'cpu\n'
      ;;
  esac
}

ask_yes_no() {
  local prompt default answer hint
  prompt="$1"
  default="$2"
  if [[ "${default}" == "true" ]]; then
    hint="Y/n"
  else
    hint="y/N"
  fi

  while true; do
    printf '%s [%s]: ' "${prompt}" "${hint}" >&2
    if ! IFS= read -r -u "${INTERACTIVE_INPUT_FD}" answer; then
      die "无法读取交互输入，请使用 --non-interactive 和显式参数"
    fi
    case "${answer}" in
      '') printf '%s\n' "${default}"; return ;;
      y|Y|yes|YES|Yes|是) printf 'true\n'; return ;;
      n|N|no|NO|No|否) printf 'false\n'; return ;;
      *) printf '请输入 y 或 n。\n' >&2 ;;
    esac
  done
}

prepare_interactive_input() {
  if [[ -t 0 ]]; then
    INTERACTIVE_INPUT_FD=0
    return 0
  fi
  if { exec 3</dev/tty; } 2>/dev/null; then
    INTERACTIVE_INPUT_FD=3
    return 0
  fi
  return 1
}

yaml_template_name_for_platform() {
  case "$1" in
    cpu) printf 'deploy/compose/templates/cpu.yml\n' ;;
    cuda) printf 'deploy/compose/templates/cuda.yml\n' ;;
    jetson) printf 'deploy/compose/templates/jetson.yml\n' ;;
    rknn) printf 'deploy/compose/templates/rknn.yml\n' ;;
    *) return 1 ;;
  esac
}

insert_block_before_top_level_key() {
  local key block input output
  key="$1"
  block="$2"
  input="$3"
  output="$4"
  awk -v marker="${key}:" '
    FNR == NR {
      block = block $0 ORS
      next
    }
    !inserted && $0 == marker {
      printf "%s", block
      inserted = 1
    }
    { print }
    END {
      if (!inserted) {
        exit 42
      }
    }
  ' "${block}" "${input}" >"${output}" || die "无法在模板中定位 ${key} 配置"
}

insert_block_after_top_level_key() {
  local key block input output
  key="$1"
  block="$2"
  input="$3"
  output="$4"
  awk -v marker="${key}:" '
    FNR == NR {
      block = block $0 ORS
      next
    }
    {
      print
      if (!inserted && $0 == marker) {
        printf "%s", block
        inserted = 1
      }
    }
    END {
      if (!inserted) {
        exit 42
      }
    }
  ' "${block}" "${input}" >"${output}" || die "无法在模板中定位 ${key} 配置"
}

apply_nju_mirror() {
  local input output
  input="$1"
  output="$2"
  sed 's#ghcr\.io/#ghcr.nju.edu.cn/#g' "${input}" >"${output}"
}

download_url() {
  local url output
  url="$1"
  output="$2"

  if command -v curl >/dev/null 2>&1; then
    curl --fail --location --silent --show-error \
      --retry 3 --connect-timeout 10 --max-time 60 \
      "${url}" --output "${output}"
  elif command -v wget >/dev/null 2>&1; then
    wget --quiet --timeout=60 --tries=3 --output-document="${output}" "${url}"
  else
    die "下载配置需要 curl 或 wget"
  fi
}

github_raw_fallback_url() {
  local url
  url="$1"
  [[ -n "${GH_PROXY_BASE_URL}" ]] || return 1
  case "${url}" in
    https://raw.githubusercontent.com/*)
      printf '%s/%s\n' "${GH_PROXY_BASE_URL%/}" "${url}"
      ;;
    *) return 1 ;;
  esac
}

download_file() {
  local url output fallback_url
  url="$1"
  output="$2"

  if download_url "${url}" "${output}"; then
    return 0
  fi

  fallback_url="$(github_raw_fallback_url "${url}" || true)"
  [[ -n "${fallback_url}" ]] || return 1
  warn "GitHub Raw 下载失败，尝试 GHProxy：${fallback_url}"
  download_url "${fallback_url}" "${output}"
}

resolve_source_file() {
  local relative_path local_path downloaded_path downloaded_dir url
  relative_path="$1"
  local_path="${PROJECT_DIR}/${relative_path}"
  if [[ -f "${local_path}" ]]; then
    printf '%s\n' "${local_path}"
    return
  fi

  downloaded_path="${TEMP_DIR}/sources/${relative_path}"
  downloaded_dir="$(dirname -- "${downloaded_path}")"
  mkdir -p -- "${downloaded_dir}"
  if [[ ! -s "${downloaded_path}" ]]; then
    url="${CONFIG_BASE_URL%/}/${relative_path}"
    if ! download_file "${url}" "${downloaded_path}"; then
      die "部署源文件下载失败：${url}"
    fi
    [[ -s "${downloaded_path}" ]] || die "下载到的部署源文件为空：${url}"
    printf '已下载部署源文件：%s\n' "${relative_path}" >&2
  fi
  printf '%s\n' "${downloaded_path}"
}

sync_config_file() {
  local relative_path source_path target_path target_dir source_abs target_abs temp_file url
  relative_path="$1"
  source_path="${PROJECT_DIR}/${relative_path}"
  target_path="${OUTPUT_DIR}/${relative_path}"
  target_dir="$(dirname -- "${target_path}")"

  if [[ -e "${target_path}" && ! -f "${target_path}" ]]; then
    die "配置目标不是普通文件：${target_path}"
  fi
  if [[ -s "${target_path}" && "${FORCE_CONFIGS}" != "true" ]]; then
    printf '保留已有配置：%s\n' "${target_path}"
    return
  fi

  mkdir -p -- "${target_dir}"
  target_abs="$(CDPATH= cd -- "${target_dir}" && pwd)/$(basename -- "${target_path}")"
  source_abs=""
  if [[ -f "${source_path}" ]]; then
    source_abs="$(CDPATH= cd -- "$(dirname -- "${source_path}")" && pwd)/$(basename -- "${source_path}")"
  fi

  temp_file="$(mktemp "${target_dir}/.video-ba-config.XXXXXX")"
  if [[ -n "${source_abs}" && "${source_abs}" != "${target_abs}" ]]; then
    cp -- "${source_path}" "${temp_file}"
    printf '已复制配置：%s\n' "${target_path}"
  else
    url="${CONFIG_BASE_URL%/}/${relative_path}"
    if ! download_file "${url}" "${temp_file}"; then
      unlink "${temp_file}" 2>/dev/null || true
      die "配置下载失败：${url}"
    fi
    [[ -s "${temp_file}" ]] || {
      unlink "${temp_file}" 2>/dev/null || true
      die "下载到的配置为空：${url}"
    }
    printf '已下载配置：%s\n' "${target_path}"
  fi

  chmod 0644 "${temp_file}"
  mv -f -- "${temp_file}" "${target_path}"
}

required_file_scope_enabled() {
  case "$1" in
    always) return 0 ;;
    mqtt) [[ "${ENABLE_MQTT}" == "true" ]] ;;
    rabbitmq) [[ "${ENABLE_RABBITMQ}" == "true" ]] ;;
    mediamtx) [[ "${ENABLE_MEDIAMTX}" == "true" ]] ;;
    *) die "必要文件清单包含未知 scope：$1" ;;
  esac
}

prepare_required_files() {
  local manifest kind scope relative_path extra
  manifest="$(resolve_source_file "deploy/compose/required-files.txt")"

  while read -r kind scope relative_path extra; do
    [[ -n "${kind}" && "${kind}" != \#* ]] || continue
    [[ -n "${scope}" && -n "${relative_path}" && -z "${extra}" ]] || \
      die "必要文件清单格式错误：${kind} ${scope} ${relative_path} ${extra}"
    case "${relative_path}" in
      /*|.|..|*../*|../*|*/..) die "必要文件清单包含不安全路径：${relative_path}" ;;
    esac
    required_file_scope_enabled "${scope}" || continue
    case "${kind}" in
      dir) mkdir -p -- "${OUTPUT_DIR}/${relative_path}" ;;
      file) sync_config_file "${relative_path}" ;;
      *) die "必要文件清单包含未知 kind：${kind}" ;;
    esac
  done <"${manifest}"
}

random_secret() {
  od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
}

ask_text() {
  local prompt default answer
  prompt="$1"
  default="$2"
  if [[ -n "${default}" ]]; then
    printf '%s [%s]: ' "${prompt}" "${default}" >&2
  else
    printf '%s（可留空）: ' "${prompt}" >&2
  fi
  if ! IFS= read -r -u "${INTERACTIVE_INPUT_FD}" answer; then
    die "无法读取交互输入"
  fi
  printf '%s\n' "${answer:-${default}}"
}

ask_secret() {
  local prompt default answer
  prompt="$1"
  default="$2"
  printf '%s（回车自动生成/沿用）: ' "${prompt}" >&2
  if ! IFS= read -r -s -u "${INTERACTIVE_INPUT_FD}" answer; then
    printf '\n' >&2
    die "无法读取交互输入"
  fi
  printf '\n' >&2
  printf '%s\n' "${answer:-${default}}"
}

validate_env_value() {
  local name value
  name="$1"
  value="$2"
  [[ "${value}" != *$'\n'* && "${value}" != *$'\r'* ]] || die "${name} 不能包含换行"
}

validate_http_port() {
  local value
  value="$1"
  [[ "${value}" =~ ^[1-9][0-9]{0,4}$ ]] || die "HTTP_PORT 必须是 1 到 65535 之间的整数"
  ((value <= 65535)) || die "HTTP_PORT 必须是 1 到 65535 之间的整数"
}

quote_env_value() {
  local value escaped_quote
  value="$1"
  escaped_quote="\\'"
  value="${value//\\/\\\\}"
  value="${value//\'/${escaped_quote}}"
  printf "'%s'" "${value}"
}

write_env_pair() {
  local name value
  name="$1"
  value="$2"
  validate_env_value "${name}" "${value}"
  printf '%s=%s\n' "${name}" "$(quote_env_value "${value}")"
}

initialize_env_values() {
  ENV_RELEASE="${VIDEO_BA_PIPE_RELEASE:-stable}"
  ENV_COMPANY_NAME="${COMPANY_NAME:-码全科技}"
  ENV_HTTP_PORT="${HTTP_PORT:-8080}"
  ENV_DB_NAME="${DB_NAME:-video_ba_pipe}"
  ENV_DB_USER="${DB_USER:-video_ba_pipe}"
  ENV_DB_PASSWORD="${DB_PASSWORD:-$(random_secret)}"
  ENV_PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-}"
  ENV_NODE_ID="${NODE_ID:-}"
  ENV_DEVICE_MODEL_CODE="${DEVICE_MODEL_CODE:-}"
  ENV_JWT_SECRET="${JWT_SECRET:-$(random_secret)}"
  ENV_MEDIA_SIGNING_SECRET="${MEDIA_URL_SIGNING_SECRET:-$(random_secret)}"
  ENV_MEDIAMTX_API_USER="${MEDIAMTX_API_USER:-video-ba-api}"
  ENV_MEDIAMTX_API_PASSWORD="${MEDIAMTX_API_PASSWORD:-$(random_secret)}"
  ENV_MEDIAMTX_PUBLIC_HOST="${MEDIAMTX_WEBRTC_PUBLIC_HOST:-}"
  ENV_MEDIAMTX_ADDITIONAL_HOSTS="${MEDIAMTX_WEBRTC_ADDITIONAL_HOSTS:-}"
  ENV_RABBITMQ_USER="${RABBITMQ_DEFAULT_USER:-admin}"
  ENV_RABBITMQ_PASSWORD="${RABBITMQ_DEFAULT_PASS:-$(random_secret)}"
}

collect_env_answers() {
  ENV_RELEASE="$(ask_text '镜像发布版本/commit' "${ENV_RELEASE}")"
  ENV_COMPANY_NAME="$(ask_text '页面显示的公司名称' "${ENV_COMPANY_NAME}")"
  ENV_HTTP_PORT="$(ask_text '前端 HTTP 端口' "${ENV_HTTP_PORT}")"
  ENV_DB_NAME="$(ask_text 'PostgreSQL 数据库名' "${ENV_DB_NAME}")"
  ENV_DB_USER="$(ask_text 'PostgreSQL 用户名' "${ENV_DB_USER}")"
  ENV_DB_PASSWORD="$(ask_secret 'PostgreSQL 密码' "${ENV_DB_PASSWORD}")"
  ENV_PUBLIC_BASE_URL="$(ask_text '外部访问地址，例如 https://video.example.com' "${ENV_PUBLIC_BASE_URL}")"
  ENV_NODE_ID="$(ask_text '节点 ID（留空时自动识别并持久化）' "${ENV_NODE_ID}")"
  ENV_DEVICE_MODEL_CODE="$(ask_text '设备型号代码（不使用模板迁移时可留空）' "${ENV_DEVICE_MODEL_CODE}")"
  ENV_JWT_SECRET="$(ask_secret 'JWT 密钥' "${ENV_JWT_SECRET}")"
  ENV_MEDIA_SIGNING_SECRET="$(ask_secret '媒体 URL 签名密钥' "${ENV_MEDIA_SIGNING_SECRET}")"

  if [[ "${ENABLE_MEDIAMTX}" == "true" ]]; then
    ENV_MEDIAMTX_API_USER="$(ask_text 'MediaMTX API 用户名' "${ENV_MEDIAMTX_API_USER}")"
    ENV_MEDIAMTX_API_PASSWORD="$(ask_secret 'MediaMTX API 密码' "${ENV_MEDIAMTX_API_PASSWORD}")"
    ENV_MEDIAMTX_PUBLIC_HOST="$(ask_text 'WebRTC 信令对浏览器可达的主机/IP' "${ENV_MEDIAMTX_PUBLIC_HOST}")"
    ENV_MEDIAMTX_ADDITIONAL_HOSTS="$(ask_text 'WebRTC 可达的宿主机 IP/域名（逗号分隔）' "${ENV_MEDIAMTX_ADDITIONAL_HOSTS}")"
  fi

  if [[ "${ENABLE_RABBITMQ}" == "true" ]]; then
    ENV_RABBITMQ_USER="$(ask_text 'RabbitMQ 管理用户名' "${ENV_RABBITMQ_USER}")"
    ENV_RABBITMQ_PASSWORD="$(ask_secret 'RabbitMQ 管理密码' "${ENV_RABBITMQ_PASSWORD}")"
  fi
}

write_env_file() {
  local env_dir env_temp
  validate_http_port "${ENV_HTTP_PORT}"
  env_dir="$(dirname -- "${ENV_FILE}")"
  [[ -d "${env_dir}" ]] || mkdir -p -- "${env_dir}"
  env_temp="$(mktemp "${env_dir}/.video-ba-env.XXXXXX")"

  {
    printf '# 由 scripts/generate_compose.sh 自动生成。\n'
    printf '# 请妥善保管：此文件包含数据库和应用密钥。\n\n'
    write_env_pair VIDEO_BA_PIPE_RELEASE "${ENV_RELEASE}"
    write_env_pair COMPANY_NAME "${ENV_COMPANY_NAME}"
    write_env_pair HTTP_PORT "${ENV_HTTP_PORT}"
    write_env_pair DB_BACKEND "postgres"
    write_env_pair DB_HOST "postgres"
    write_env_pair DB_PORT "5432"
    write_env_pair DB_NAME "${ENV_DB_NAME}"
    write_env_pair DB_USER "${ENV_DB_USER}"
    write_env_pair DB_PASSWORD "${ENV_DB_PASSWORD}"
    write_env_pair POSTGRES_DB "${ENV_DB_NAME}"
    write_env_pair POSTGRES_USER "${ENV_DB_USER}"
    write_env_pair POSTGRES_PASSWORD "${ENV_DB_PASSWORD}"
    write_env_pair PUBLIC_BASE_URL "${ENV_PUBLIC_BASE_URL}"
    write_env_pair NODE_ID "${ENV_NODE_ID}"
    write_env_pair DEVICE_MODEL_CODE "${ENV_DEVICE_MODEL_CODE}"
    write_env_pair JWT_SECRET "${ENV_JWT_SECRET}"
    write_env_pair MEDIA_URL_SIGNING_ENABLED "true"
    write_env_pair MEDIA_URL_SIGNING_SECRET "${ENV_MEDIA_SIGNING_SECRET}"

    if [[ "${ENABLE_MEDIAMTX}" == "true" ]]; then
      printf '\n# MediaMTX / WebRTC\n'
      write_env_pair MEDIAMTX_ENABLED "true"
      write_env_pair MEDIAMTX_API_USER "${ENV_MEDIAMTX_API_USER}"
      write_env_pair MEDIAMTX_API_PASSWORD "${ENV_MEDIAMTX_API_PASSWORD}"
      write_env_pair MEDIAMTX_WEBRTC_PUBLIC_HOST "${ENV_MEDIAMTX_PUBLIC_HOST}"
      write_env_pair MEDIAMTX_WEBRTC_ADDITIONAL_HOSTS "${ENV_MEDIAMTX_ADDITIONAL_HOSTS}"
    else
      write_env_pair MEDIAMTX_ENABLED "false"
    fi

    if [[ "${ENABLE_RABBITMQ}" == "true" ]]; then
      printf '\n# RabbitMQ\n'
      write_env_pair RABBITMQ_DEFAULT_USER "${ENV_RABBITMQ_USER}"
      write_env_pair RABBITMQ_DEFAULT_PASS "${ENV_RABBITMQ_PASSWORD}"
    fi
  } >"${env_temp}"

  chmod 0600 "${env_temp}"
  mv -f -- "${env_temp}" "${ENV_FILE}"
  printf '已生成环境文件：%s（权限 600）\n' "${ENV_FILE}"
}

parameterize_generated_compose() {
  local input output
  input="$1"
  output="$2"
  sed \
    -e 's#- "8080:80"#- "${HTTP_PORT:-8080}:80"#g' \
    "${input}" >"${output}"
}

while (($# > 0)); do
  case "$1" in
    -p|--platform)
      (($# >= 2)) || die "$1 缺少参数"
      PLATFORM="$2"
      PLATFORM_SET=true
      shift 2
      ;;
    --platform=*)
      PLATFORM="${1#*=}"
      PLATFORM_SET=true
      shift
      ;;
    -o|--output)
      (($# >= 2)) || die "$1 缺少参数"
      OUTPUT_FILE="$2"
      shift 2
      ;;
    --output=*)
      OUTPUT_FILE="${1#*=}"
      shift
      ;;
    --with-mqtt|--mqtt)
      ENABLE_MQTT=true
      MQTT_SET=true
      shift
      ;;
    --without-mqtt|--no-mqtt)
      ENABLE_MQTT=false
      MQTT_SET=true
      shift
      ;;
    --with-rabbitmq|--rabbitmq)
      ENABLE_RABBITMQ=true
      RABBITMQ_SET=true
      shift
      ;;
    --without-rabbitmq|--no-rabbitmq)
      ENABLE_RABBITMQ=false
      RABBITMQ_SET=true
      shift
      ;;
    --with-mediamtx|--mediamtx)
      ENABLE_MEDIAMTX=true
      MEDIAMTX_SET=true
      shift
      ;;
    --without-mediamtx|--no-mediamtx)
      ENABLE_MEDIAMTX=false
      MEDIAMTX_SET=true
      shift
      ;;
    --nju-mirror)
      USE_NJU_MIRROR=true
      NJU_MIRROR_SET=true
      shift
      ;;
    --no-nju-mirror)
      USE_NJU_MIRROR=false
      NJU_MIRROR_SET=true
      shift
      ;;
    --download-configs|--fetch-configs)
      DOWNLOAD_CONFIGS=true
      shift
      ;;
    --no-download-configs|--no-fetch-configs)
      DOWNLOAD_CONFIGS=false
      shift
      ;;
    --force-configs)
      DOWNLOAD_CONFIGS=true
      FORCE_CONFIGS=true
      shift
      ;;
    --config-base-url)
      (($# >= 2)) || die "$1 缺少参数"
      CONFIG_BASE_URL="$2"
      shift 2
      ;;
    --config-base-url=*)
      CONFIG_BASE_URL="${1#*=}"
      shift
      ;;
    --env-file)
      (($# >= 2)) || die "$1 缺少参数"
      ENV_FILE="$2"
      GENERATE_ENV=true
      shift 2
      ;;
    --env-file=*)
      ENV_FILE="${1#*=}"
      GENERATE_ENV=true
      shift
      ;;
    --no-env-file)
      GENERATE_ENV=false
      shift
      ;;
    --force-env)
      GENERATE_ENV=true
      FORCE_ENV=true
      shift
      ;;
    --interactive)
      INTERACTIVE_MODE=true
      shift
      ;;
    --non-interactive)
      INTERACTIVE_MODE=false
      shift
      ;;
    -f|--force)
      FORCE=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *) die "未知参数：$1（使用 --help 查看帮助）" ;;
  esac
done

if [[ "${INTERACTIVE_MODE}" != "false" ]]; then
  if prepare_interactive_input; then
    if [[ "${INTERACTIVE_MODE}" == "auto" ]]; then
      INTERACTIVE_MODE=true
    fi
  elif [[ "${INTERACTIVE_MODE}" == "true" ]]; then
    die "交互模式需要可用的控制终端；请直接运行脚本，或使用 --non-interactive"
  else
    INTERACTIVE_MODE=false
  fi
fi

DETECTED_PLATFORM="$(detect_platform)"

if [[ "${INTERACTIVE_MODE}" == "true" && "${PLATFORM_SET}" == "false" ]]; then
  printf '部署平台 [auto=%s]（cpu/cuda/jetson/rknn，回车自动）: ' "${DETECTED_PLATFORM}" >&2
  if ! IFS= read -r -u "${INTERACTIVE_INPUT_FD}" selected_platform; then
    die "无法读取交互输入"
  fi
  PLATFORM="${selected_platform:-auto}"
fi

if [[ "${PLATFORM}" == "auto" ]]; then
  PLATFORM="${DETECTED_PLATFORM}"
fi

case "${PLATFORM}" in
  cpu|cuda|jetson|rknn) ;;
  *) die "不支持的平台 ${PLATFORM}；可选值为 auto、cpu、cuda、jetson、rknn" ;;
esac

if [[ "${INTERACTIVE_MODE}" == "true" ]]; then
  [[ "${MQTT_SET}" == "true" ]] || ENABLE_MQTT="$(ask_yes_no '包含内置 MQTT Broker？' "${ENABLE_MQTT}")"
  [[ "${RABBITMQ_SET}" == "true" ]] || ENABLE_RABBITMQ="$(ask_yes_no '包含内置 RabbitMQ？' "${ENABLE_RABBITMQ}")"
  [[ "${MEDIAMTX_SET}" == "true" ]] || ENABLE_MEDIAMTX="$(ask_yes_no '包含 MediaMTX 实时预览中继？' "${ENABLE_MEDIAMTX}")"
  [[ "${NJU_MIRROR_SET}" == "true" ]] || USE_NJU_MIRROR="$(ask_yes_no '将 ghcr.io 替换为 ghcr.nju.edu.cn？' "${USE_NJU_MIRROR}")"
fi

if [[ "${OUTPUT_FILE}" != "-" && -e "${OUTPUT_FILE}" && "${FORCE}" != "true" ]]; then
  if [[ "${INTERACTIVE_MODE}" == "true" ]]; then
    FORCE="$(ask_yes_no "${OUTPUT_FILE} 已存在，是否覆盖？" false)"
  fi
  [[ "${FORCE}" == "true" ]] || die "输出文件已存在：${OUTPUT_FILE}（使用 --force 覆盖）"
fi

if [[ "${OUTPUT_FILE}" != "-" ]]; then
  OUTPUT_DIR="$(dirname -- "${OUTPUT_FILE}")"
  [[ -d "${OUTPUT_DIR}" ]] || die "输出目录不存在：${OUTPUT_DIR}"
  [[ -n "${ENV_FILE}" ]] || ENV_FILE="${OUTPUT_DIR}/.env"
fi

ENV_STATUS="skipped"
if [[ "${GENERATE_ENV}" == "auto" ]]; then
  if [[ "${OUTPUT_FILE}" == "-" && -z "${ENV_FILE}" ]]; then
    GENERATE_ENV=false
  elif [[ -e "${ENV_FILE}" ]]; then
    ENV_STATUS="preserved"
    if [[ "${INTERACTIVE_MODE}" == "true" ]]; then
      GENERATE_ENV="$(ask_yes_no "${ENV_FILE} 已存在，是否重新问答并覆盖？" false)"
      [[ "${GENERATE_ENV}" == "true" ]] && FORCE_ENV=true
    else
      GENERATE_ENV=false
    fi
  elif [[ "${INTERACTIVE_MODE}" == "true" ]]; then
    GENERATE_ENV="$(ask_yes_no '通过问答生成 .env 环境文件？' true)"
  else
    GENERATE_ENV=true
  fi
fi

if [[ "${GENERATE_ENV}" == "true" ]]; then
  [[ -n "${ENV_FILE}" ]] || die "输出到标准输出时请用 --env-file 指定环境文件路径"
  if [[ -e "${ENV_FILE}" && ! -f "${ENV_FILE}" ]]; then
    die ".env 目标不是普通文件：${ENV_FILE}"
  fi
  if [[ -e "${ENV_FILE}" && "${FORCE_ENV}" != "true" ]]; then
    if [[ "${INTERACTIVE_MODE}" == "true" ]]; then
      FORCE_ENV="$(ask_yes_no "${ENV_FILE} 已存在，是否覆盖？" false)"
    fi
    [[ "${FORCE_ENV}" == "true" ]] || die "环境文件已存在：${ENV_FILE}（使用 --force-env 覆盖）"
  fi
  initialize_env_values
  if [[ "${INTERACTIVE_MODE}" == "true" ]]; then
    collect_env_answers
  fi
  validate_http_port "${ENV_HTTP_PORT}"
fi

TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/video-ba-compose.XXXXXX")"
cleanup() {
  if [[ -n "${TEMP_DIR:-}" && -d "${TEMP_DIR}" && "${TEMP_DIR}" == *video-ba-compose.* ]]; then
    rm -rf -- "${TEMP_DIR}"
  fi
}
trap cleanup EXIT

TEMPLATE_NAME="$(yaml_template_name_for_platform "${PLATFORM}")" || die "无法选择 ${PLATFORM} 模板"
TEMPLATE_FILE="$(resolve_source_file "${TEMPLATE_NAME}")"

CURRENT_FILE="${TEMP_DIR}/compose-0.yml"
cp -- "${TEMPLATE_FILE}" "${CURRENT_FILE}"
STEP=0

set_next_file() {
  STEP=$((STEP + 1))
  NEXT_FILE="${TEMP_DIR}/compose-${STEP}.yml"
}

add_service_fragment() {
  local name fragment
  name="$1"
  fragment="$(resolve_source_file "deploy/compose/fragments/${name}.service.yml")"
  set_next_file
  insert_block_before_top_level_key networks "${fragment}" "${CURRENT_FILE}" "${NEXT_FILE}"
  CURRENT_FILE="${NEXT_FILE}"
}

add_volume_fragment() {
  local name fragment
  name="$1"
  fragment="$(resolve_source_file "deploy/compose/fragments/${name}.volumes.yml")"
  set_next_file
  insert_block_after_top_level_key volumes "${fragment}" "${CURRENT_FILE}" "${NEXT_FILE}"
  CURRENT_FILE="${NEXT_FILE}"
}

if [[ "${ENABLE_MQTT}" == "true" ]]; then
  add_service_fragment mqtt
  add_volume_fragment mqtt
fi

if [[ "${ENABLE_RABBITMQ}" == "true" ]]; then
  add_service_fragment rabbitmq
  add_volume_fragment rabbitmq
fi

if [[ "${ENABLE_MEDIAMTX}" == "true" ]]; then
  add_service_fragment mediamtx
fi

if [[ "${USE_NJU_MIRROR}" == "true" ]]; then
  set_next_file
  apply_nju_mirror "${CURRENT_FILE}" "${NEXT_FILE}"
  CURRENT_FILE="${NEXT_FILE}"
fi

set_next_file
parameterize_generated_compose "${CURRENT_FILE}" "${NEXT_FILE}"
CURRENT_FILE="${NEXT_FILE}"

OPTIONAL_SERVICES=""
[[ "${ENABLE_MQTT}" == "true" ]] && OPTIONAL_SERVICES="mqtt"
if [[ "${ENABLE_RABBITMQ}" == "true" ]]; then
  OPTIONAL_SERVICES="${OPTIONAL_SERVICES:+${OPTIONAL_SERVICES},}rabbitmq"
fi
if [[ "${ENABLE_MEDIAMTX}" == "true" ]]; then
  OPTIONAL_SERVICES="${OPTIONAL_SERVICES:+${OPTIONAL_SERVICES},}mediamtx"
fi
OPTIONAL_SERVICES="${OPTIONAL_SERVICES:-none}"
MIRROR_NAME="upstream"
[[ "${USE_NJU_MIRROR}" == "true" ]] && MIRROR_NAME="nju-ghcr"

FINAL_FILE="${TEMP_DIR}/docker-compose.yml"
awk \
  -v platform="${PLATFORM}" \
  -v services="${OPTIONAL_SERVICES}" \
  -v mirror="${MIRROR_NAME}" \
  -v source="${CONFIG_BASE_URL}" '
  BEGIN {
    print "# 由 scripts/generate_compose.sh 自动生成，请通过生成器更新。"
    print "# platform=" platform " optional_services=" services " image_source=" mirror
    print "# compose_source=" source
    print ""
  }
  { print }
' "${CURRENT_FILE}" >"${FINAL_FILE}"

if [[ "${OUTPUT_FILE}" == "-" ]]; then
  if [[ "${DOWNLOAD_CONFIGS}" == "true" ]]; then
    warn "输出到标准输出时无法确定配置目录，已跳过配置文件下载"
  fi
  cat "${FINAL_FILE}"
  if [[ "${GENERATE_ENV}" == "true" ]]; then
    write_env_file >&2
  fi
  exit 0
fi

if [[ "${DOWNLOAD_CONFIGS}" == "true" ]]; then
  prepare_required_files
fi
if [[ "${GENERATE_ENV}" == "true" ]]; then
  write_env_file
  ENV_STATUS="generated"
fi

OUTPUT_TEMP="$(mktemp "${OUTPUT_DIR}/.docker-compose.generated.XXXXXX")"
cp -- "${FINAL_FILE}" "${OUTPUT_TEMP}"
chmod 0644 "${OUTPUT_TEMP}"
mv -f -- "${OUTPUT_TEMP}" "${OUTPUT_FILE}"

printf '已生成：%s\n' "${OUTPUT_FILE}"
printf '平台：%s（自动检测：%s）\n' "${PLATFORM}" "${DETECTED_PLATFORM}"
printf '可选服务：%s\n' "${OPTIONAL_SERVICES}"
printf '镜像源：%s\n' "${MIRROR_NAME}"
if [[ "${DOWNLOAD_CONFIGS}" == "true" ]]; then
  printf '依赖配置：已准备（目录 %s）\n' "${OUTPUT_DIR}"
else
  printf '依赖配置：已跳过\n'
fi
case "${ENV_STATUS}" in
  generated) printf '环境文件：已生成 %s\n' "${ENV_FILE}" ;;
  preserved) printf '环境文件：保留已有 %s\n' "${ENV_FILE}" ;;
  *) printf '环境文件：已跳过\n' ;;
esac
if [[ -n "${ENV_FILE}" && -f "${ENV_FILE}" ]]; then
  printf '启动命令：docker compose --env-file %q -f %q up -d\n' "${ENV_FILE}" "${OUTPUT_FILE}"
else
  printf '启动命令：docker compose -f %q up -d\n' "${OUTPUT_FILE}"
fi
