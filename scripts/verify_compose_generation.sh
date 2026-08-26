#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)"
TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/video-ba-compose-verify.XXXXXX")"

cleanup() {
  if [[ -d "${TEMP_DIR}" && "${TEMP_DIR}" == *video-ba-compose-verify.* ]]; then
    rm -rf -- "${TEMP_DIR}"
  fi
}
trap cleanup EXIT

validate_yaml() {
  local compose_file
  compose_file="$1"
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    HTTP_PORT=8080 docker compose -f "${compose_file}" config --quiet
    return
  fi
  if command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' >/dev/null 2>&1; then
    python3 -c 'import sys, yaml; data=yaml.safe_load(open(sys.argv[1], encoding="utf-8")); assert isinstance(data.get("services"), dict)' "${compose_file}"
    return
  fi
  grep -q '^services:$' "${compose_file}"
  grep -q '^networks:$' "${compose_file}"
  grep -q '^volumes:$' "${compose_file}"
}

for platform in cpu cuda jetson rknn; do
  base_file="${TEMP_DIR}/${platform}-base.yml"
  full_file="${TEMP_DIR}/${platform}-full.yml"

  "${SCRIPT_DIR}/generate_compose.sh" \
    --non-interactive \
    --platform "${platform}" \
    --without-mqtt \
    --without-rabbitmq \
    --without-mediamtx \
    --no-nju-mirror \
    --no-download-configs \
    --no-env-file \
    --output "${base_file}" >/dev/null

  "${SCRIPT_DIR}/generate_compose.sh" \
    --non-interactive \
    --platform "${platform}" \
    --with-mqtt \
    --with-rabbitmq \
    --with-mediamtx \
    --nju-mirror \
    --no-download-configs \
    --no-env-file \
    --output "${full_file}" >/dev/null

  validate_yaml "${base_file}"
  validate_yaml "${full_file}"

  for service in mqtt rabbitmq mediamtx; do
    if grep -q "^  ${service}:" "${base_file}"; then
      printf 'unexpected %s in %s base output\n' "${service}" "${platform}" >&2
      exit 1
    fi
    grep -q "^  ${service}:" "${full_file}"
  done

  grep -q 'ghcr.nju.edu.cn/' "${full_file}"
  if grep -q 'docker.nju.edu.cn/' "${full_file}"; then
    printf 'Docker Hub image was unexpectedly rewritten for %s\n' "${platform}" >&2
    exit 1
  fi
  grep -q '"${HTTP_PORT:-8080}:80"' "${full_file}"
done

required_dir="${TEMP_DIR}/required-files"
mkdir -p -- "${required_dir}"
"${SCRIPT_DIR}/generate_compose.sh" \
  --non-interactive \
  --platform cpu \
  --with-mqtt \
  --with-mediamtx \
  --no-nju-mirror \
  --force-configs \
  --no-env-file \
  --output "${required_dir}/docker-compose.yml" >/dev/null

test -d "${required_dir}/data"
test -s "${required_dir}/frontend/nginx.conf"
test -s "${required_dir}/deploy/mosquitto.conf"
test -s "${required_dir}/mediamtx.yml"

env_dir="${TEMP_DIR}/env-file"
mkdir -p -- "${env_dir}"
COMPANY_NAME="O'Reilly" HTTP_PORT=18080 \
  "${SCRIPT_DIR}/generate_compose.sh" \
    --non-interactive \
    --platform cpu \
    --no-nju-mirror \
    --no-download-configs \
    --force-env \
    --output "${env_dir}/docker-compose.yml" >/dev/null

grep -Fq "COMPANY_NAME='O\\'Reilly'" "${env_dir}/.env"
grep -Fq "HTTP_PORT='18080'" "${env_dir}/.env"
validate_yaml "${env_dir}/docker-compose.yml"

printf 'Compose generation matrix verified successfully.\n'
