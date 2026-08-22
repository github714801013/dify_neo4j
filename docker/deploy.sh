#!/usr/bin/env bash
set -euo pipefail

# 使用 Docker buildx 在本机构建 Dify API/Web 镜像，打包后通过 SSH 部署到远端机器。
# 远端地址、数据库、Redis、密钥等配置均从本目录 .env 读取。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd -P)"
ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/.env}"

if [ ! -f "${ENV_FILE}" ]; then
  echo "缺少配置文件: ${ENV_FILE}" >&2
  echo "请先在 docker/.env 中配置 REMOTE、APP_HOST、DB_*、REDIS_* 等变量。" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "${ENV_FILE}"
set +a

: "${REMOTE:?请在 docker/.env 中设置 REMOTE，例如 ji99@10.1.14.177}"
: "${REMOTE_DIR:?请在 docker/.env 中设置 REMOTE_DIR，例如 /home/ji99/Project/dify-origin}"
: "${APP_HOST:?请在 docker/.env 中设置 APP_HOST}"
: "${DB_HOST:?请在 docker/.env 中设置 DB_HOST}"
: "${DB_PORT:?请在 docker/.env 中设置 DB_PORT}"
: "${DB_USERNAME:?请在 docker/.env 中设置 DB_USERNAME}"
: "${DB_PASSWORD:?请在 docker/.env 中设置 DB_PASSWORD}"
: "${DB_DATABASE:?请在 docker/.env 中设置 DB_DATABASE}"
: "${REDIS_HOST:?请在 docker/.env 中设置 REDIS_HOST}"
: "${REDIS_PORT:?请在 docker/.env 中设置 REDIS_PORT}"
: "${REDIS_PASSWORD:?请在 docker/.env 中设置 REDIS_PASSWORD}"
: "${SECRET_KEY:?请在 docker/.env 中设置 SECRET_KEY}"
: "${PLUGIN_DAEMON_KEY:?请在 docker/.env 中设置 PLUGIN_DAEMON_KEY}"
: "${PLUGIN_DIFY_INNER_API_KEY:?请在 docker/.env 中设置 PLUGIN_DIFY_INNER_API_KEY}"

IMAGE_TAG="${IMAGE_TAG:-$(date +%Y%m%d)}"
BUILDER_NAME="${BUILDER_NAME:-dify-origin-builder}"
BUILDER_DRIVER="${BUILDER_DRIVER:-docker-container}"
BUILDKIT_IMAGE="${BUILDKIT_IMAGE:-moby/buildkit:buildx-stable-1}"
BUILDKIT_NETWORK="${BUILDKIT_NETWORK:-host}"
BUILDKIT_BUILD_NETWORK="${BUILDKIT_BUILD_NETWORK:-host}"
BUILDKIT_RECREATE="${BUILDKIT_RECREATE:-0}"
BUILDKIT_REGISTRY_MIRROR="${BUILDKIT_REGISTRY_MIRROR:-}"
BUILDKIT_PROGRESS="${BUILDKIT_PROGRESS:-plain}"
BUILD_RETRIES="${BUILD_RETRIES:-3}"
PLATFORM="${PLATFORM:-linux/amd64}"
UV_INDEX_URL="${UV_INDEX_URL:-}"
NPM_CONFIG_REGISTRY="${NPM_CONFIG_REGISTRY:-}"
DB_PLUGIN_DATABASE="${DB_PLUGIN_DATABASE:-${DB_DATABASE}}"
APP_MAX_EXECUTION_TIME="${APP_MAX_EXECUTION_TIME:-3600}"
WORKFLOW_MAX_EXECUTION_TIME="${WORKFLOW_MAX_EXECUTION_TIME:-3600}"
GRAPH_RAG_ENABLED="${GRAPH_RAG_ENABLED:-false}"
GRAPH_RAG_FAIL_OPEN="${GRAPH_RAG_FAIL_OPEN:-true}"
GRAPH_RAG_DEFAULT_TIMEOUT_MS="${GRAPH_RAG_DEFAULT_TIMEOUT_MS:-1500}"
NEO4J_URI="${NEO4J_URI:-}"
NEO4J_USERNAME="${NEO4J_USERNAME:-}"
NEO4J_PASSWORD="${NEO4J_PASSWORD:-}"
NEO4J_DATABASE="${NEO4J_DATABASE:-}"
GRAPH_INDEX_WORKER_CONCURRENCY="${GRAPH_INDEX_WORKER_CONCURRENCY:-2}"
GRAPH_INDEX_CELERY_WORKER_CONCURRENCY="${GRAPH_INDEX_CELERY_WORKER_CONCURRENCY:-1}"
GRAPH_INDEX_MAX_RETRIES="${GRAPH_INDEX_MAX_RETRIES:-5}"
GRAPH_INDEX_RETRY_BASE_SECONDS="${GRAPH_INDEX_RETRY_BASE_SECONDS:-30}"
GRAPH_RECONCILE_INTERVAL_MINUTES="${GRAPH_RECONCILE_INTERVAL_MINUTES:-30}"
ENABLE_GRAPH_RECONCILE_TASK="${ENABLE_GRAPH_RECONCILE_TASK:-false}"
REMOTE_IMAGE_TAR="${REMOTE_IMAGE_TAR:-${REMOTE_DIR}/dify-origin-images-${IMAGE_TAG}.tar}"
LOCAL_IMAGE_TAR="${LOCAL_IMAGE_TAR:-$(mktemp -u -t "dify-origin-images-${IMAGE_TAG}.XXXXXX.tar")}"

API_IMAGE="dify-origin-api:${IMAGE_TAG}"
WEB_IMAGE="dify-origin-web:${IMAGE_TAG}"
PLUGIN_IMAGE="langgenius/dify-plugin-daemon:0.6.3-local"
SANDBOX_IMAGE="langgenius/dify-sandbox:0.2.15"
SQUID_IMAGE="ubuntu/squid:latest"
BUSYBOX_IMAGE="busybox:latest"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "缺少命令: $1" >&2
    exit 1
  }
}

ensure_builder() {
  local driver_opts=(
    --driver-opt "image=${BUILDKIT_IMAGE}"
    --driver-opt "network=${BUILDKIT_NETWORK}"
  )
  local config_args=()
  local buildkit_config=""

  if [ -n "${BUILDKIT_REGISTRY_MIRROR}" ]; then
    buildkit_config="$(mktemp -t dify-buildkitd.XXXXXX.toml)"
    cat >"${buildkit_config}" <<EOF
[registry."docker.io"]
  mirrors = ["${BUILDKIT_REGISTRY_MIRROR}"]
EOF
    config_args=(--config "${buildkit_config}")
  fi

  if [ "${BUILDKIT_RECREATE}" = "1" ] && docker buildx inspect "${BUILDER_NAME}" >/dev/null 2>&1; then
    docker buildx rm "${BUILDER_NAME}" >/dev/null
  fi

  if ! docker buildx inspect "${BUILDER_NAME}" >/dev/null 2>&1; then
    docker buildx create \
      --name "${BUILDER_NAME}" \
      --driver "${BUILDER_DRIVER}" \
      "${driver_opts[@]}" \
      "${config_args[@]}" \
      --use
  else
    docker buildx use "${BUILDER_NAME}"
  fi
  docker buildx inspect --bootstrap >/dev/null
}

build_image() {
  local dockerfile="$1"
  local image="$2"
  local commit_sha="$3"
  local attempt=1
  local build_args=(--build-arg "COMMIT_SHA=${commit_sha}")
  local output_tar
  output_tar="$(mktemp -t 'dify-build-image.XXXXXX.tar')"
  rm -f "${output_tar}"

  if [ -n "${UV_INDEX_URL}" ]; then
    build_args+=(--build-arg "UV_INDEX_URL=${UV_INDEX_URL}")
  fi
  if [ -n "${NPM_CONFIG_REGISTRY}" ]; then
    build_args+=(--build-arg "NPM_CONFIG_REGISTRY=${NPM_CONFIG_REGISTRY}")
  fi

  while true; do
    if env -u HTTP_PROXY -u HTTPS_PROXY -u NO_PROXY -u http_proxy -u https_proxy -u no_proxy \
      docker buildx build --builder "${BUILDER_NAME}" --platform "${PLATFORM}" --network "${BUILDKIT_BUILD_NETWORK}" --progress "${BUILDKIT_PROGRESS}" --output "type=docker,dest=${output_tar}" \
      -f "${dockerfile}" "${build_args[@]}" -t "${image}" .; then
      if docker load -i "${output_tar}" >/dev/null; then
        rm -f "${output_tar}"
        return
      fi
    fi

    rm -f "${output_tar}"
    output_tar="$(mktemp -t 'dify-build-image.XXXXXX.tar')"
    rm -f "${output_tar}"

    if [ "${attempt}" -ge "${BUILD_RETRIES}" ]; then
      echo "镜像构建失败: ${image}" >&2
      return 1
    fi

    echo "镜像构建失败，准备重试: ${image} (${attempt}/${BUILD_RETRIES})" >&2
    attempt=$((attempt + 1))
    sleep 5
  done
}

pull_with_mirror() {
  local source_image="$1"
  local target_image="$2"
  local mirror_image="${DOCKER_MIRROR_PREFIX:-docker.1ms.run}/${source_image}"

  if docker image inspect "${target_image}" >/dev/null 2>&1; then
    return
  fi

  if docker pull "${mirror_image}"; then
    docker tag "${mirror_image}" "${target_image}"
    return
  fi

  docker pull "${target_image}"
}

write_runtime_env_file() {
  local file="$1"

  cat >"${file}" <<EOF
SECRET_KEY=${SECRET_KEY}
WECOM_LONG_LINK_ENABLED=${WECOM_LONG_LINK_ENABLED:-false}
DEPLOY_ENV=${DEPLOY_ENV:-PRODUCTION}
EDITION=${EDITION:-SELF_HOSTED}
DB_TYPE=oceanbase
DB_HOST=${DB_HOST}
DB_PORT=${DB_PORT}
DB_USERNAME=${DB_USERNAME}
DB_PASSWORD=${DB_PASSWORD}
DB_DATABASE=${DB_DATABASE}
DB_PLUGIN_DATABASE=${DB_PLUGIN_DATABASE}
SQLALCHEMY_POOL_SIZE=${SQLALCHEMY_POOL_SIZE:-30}
SQLALCHEMY_MAX_OVERFLOW=${SQLALCHEMY_MAX_OVERFLOW:-10}
SQLALCHEMY_POOL_RECYCLE=${SQLALCHEMY_POOL_RECYCLE:-3600}
SQLALCHEMY_POOL_PRE_PING=${SQLALCHEMY_POOL_PRE_PING:-false}
SERVER_WORKER_AMOUNT=${SERVER_WORKER_AMOUNT:-1}
SERVER_WORKER_CONNECTIONS=${SERVER_WORKER_CONNECTIONS:-10}
GUNICORN_TIMEOUT=${GUNICORN_TIMEOUT:-900}
APP_MAX_EXECUTION_TIME=${APP_MAX_EXECUTION_TIME}
WORKFLOW_MAX_EXECUTION_TIME=${WORKFLOW_MAX_EXECUTION_TIME}
GRAPH_RAG_ENABLED=${GRAPH_RAG_ENABLED}
GRAPH_RAG_FAIL_OPEN=${GRAPH_RAG_FAIL_OPEN}
GRAPH_RAG_DEFAULT_TIMEOUT_MS=${GRAPH_RAG_DEFAULT_TIMEOUT_MS}
NEO4J_URI=${NEO4J_URI}
NEO4J_USERNAME=${NEO4J_USERNAME}
NEO4J_PASSWORD=${NEO4J_PASSWORD}
NEO4J_DATABASE=${NEO4J_DATABASE}
GRAPH_INDEX_WORKER_CONCURRENCY=${GRAPH_INDEX_WORKER_CONCURRENCY}
GRAPH_INDEX_CELERY_WORKER_CONCURRENCY=${GRAPH_INDEX_CELERY_WORKER_CONCURRENCY}
GRAPH_INDEX_MAX_RETRIES=${GRAPH_INDEX_MAX_RETRIES}
GRAPH_INDEX_RETRY_BASE_SECONDS=${GRAPH_INDEX_RETRY_BASE_SECONDS}
GRAPH_RECONCILE_INTERVAL_MINUTES=${GRAPH_RECONCILE_INTERVAL_MINUTES}
ENABLE_GRAPH_RECONCILE_TASK=${ENABLE_GRAPH_RECONCILE_TASK}
REDIS_HOST=${REDIS_HOST}
REDIS_PORT=${REDIS_PORT}
REDIS_USERNAME=${REDIS_USERNAME:-}
REDIS_PASSWORD=${REDIS_PASSWORD}
REDIS_DB=${REDIS_DB:-0}
REDIS_USE_SSL=${REDIS_USE_SSL:-false}
CELERY_BROKER_URL=${CELERY_BROKER_URL:-redis://:${REDIS_PASSWORD}@${REDIS_HOST}:${REDIS_PORT}/1}
CELERY_BACKEND=${CELERY_BACKEND:-redis}
VECTOR_STORE=oceanbase
OCEANBASE_VECTOR_HOST=${OCEANBASE_VECTOR_HOST:-${DB_HOST}}
OCEANBASE_VECTOR_PORT=${OCEANBASE_VECTOR_PORT:-${DB_PORT}}
OCEANBASE_VECTOR_USER=${OCEANBASE_VECTOR_USER:-${DB_USERNAME}}
OCEANBASE_VECTOR_PASSWORD=${OCEANBASE_VECTOR_PASSWORD:-${DB_PASSWORD}}
OCEANBASE_VECTOR_DATABASE=${OCEANBASE_VECTOR_DATABASE:-${DB_DATABASE}}
OCEANBASE_ENABLE_HYBRID_SEARCH=${OCEANBASE_ENABLE_HYBRID_SEARCH:-false}
OCEANBASE_FULLTEXT_PARSER=${OCEANBASE_FULLTEXT_PARSER:-ik}
CONSOLE_API_URL=${CONSOLE_API_URL:-http://${APP_HOST}:${EXPOSE_API_PORT:-5001}}
SERVER_CONSOLE_API_URL=${SERVER_CONSOLE_API_URL:-http://api:5001}
CONSOLE_WEB_URL=${CONSOLE_WEB_URL:-http://${APP_HOST}:${EXPOSE_WEB_PORT:-3000}}
SERVICE_API_URL=${SERVICE_API_URL:-http://${APP_HOST}:${EXPOSE_API_PORT:-5001}}
APP_API_URL=${APP_API_URL:-http://${APP_HOST}:${EXPOSE_API_PORT:-5001}}
APP_WEB_URL=${APP_WEB_URL:-http://${APP_HOST}:${EXPOSE_WEB_PORT:-3000}}
FILES_URL=${FILES_URL:-http://${APP_HOST}:${EXPOSE_API_PORT:-5001}}
CONSOLE_CORS_ALLOW_ORIGINS=${CONSOLE_CORS_ALLOW_ORIGINS:-*}
WEB_API_CORS_ALLOW_ORIGINS=${WEB_API_CORS_ALLOW_ORIGINS:-*}
STORAGE_TYPE=${STORAGE_TYPE:-opendal}
OPENDAL_SCHEME=${OPENDAL_SCHEME:-fs}
OPENDAL_FS_ROOT=${OPENDAL_FS_ROOT:-storage}
CODE_EXECUTION_ENDPOINT=${CODE_EXECUTION_ENDPOINT:-http://sandbox:8194}
CODE_EXECUTION_API_KEY=${CODE_EXECUTION_API_KEY:-dify-sandbox}
SANDBOX_API_KEY=${SANDBOX_API_KEY:-dify-sandbox}
SANDBOX_ENABLE_NETWORK=${SANDBOX_ENABLE_NETWORK:-true}
SANDBOX_HTTP_PROXY=${SANDBOX_HTTP_PROXY:-http://ssrf_proxy:3128}
SANDBOX_HTTPS_PROXY=${SANDBOX_HTTPS_PROXY:-http://ssrf_proxy:3128}
SANDBOX_PORT=${SANDBOX_PORT:-8194}
SSRF_PROXY_HTTP_URL=${SSRF_PROXY_HTTP_URL:-http://ssrf_proxy:3128}
SSRF_PROXY_HTTPS_URL=${SSRF_PROXY_HTTPS_URL:-http://ssrf_proxy:3128}
SSRF_HTTP_PORT=${SSRF_HTTP_PORT:-3128}
SSRF_PROXY_ALLOW_PRIVATE_IPS=${SSRF_PROXY_ALLOW_PRIVATE_IPS:-}
SSRF_PROXY_ALLOW_PRIVATE_DOMAINS=${SSRF_PROXY_ALLOW_PRIVATE_DOMAINS:-}
PLUGIN_DAEMON_URL=${PLUGIN_DAEMON_URL:-http://plugin_daemon:5002}
PLUGIN_DAEMON_PORT=${PLUGIN_DAEMON_PORT:-5002}
EXPOSE_PLUGIN_DAEMON_PORT=${EXPOSE_PLUGIN_DAEMON_PORT:-5002}
PLUGIN_DAEMON_KEY=${PLUGIN_DAEMON_KEY}
PLUGIN_DIFY_INNER_API_URL=${PLUGIN_DIFY_INNER_API_URL:-http://api:5001}
PLUGIN_DIFY_INNER_API_KEY=${PLUGIN_DIFY_INNER_API_KEY}
PLUGIN_DEBUGGING_HOST=${PLUGIN_DEBUGGING_HOST:-0.0.0.0}
PLUGIN_DEBUGGING_PORT=${PLUGIN_DEBUGGING_PORT:-5003}
EXPOSE_PLUGIN_DEBUGGING_HOST=${EXPOSE_PLUGIN_DEBUGGING_HOST:-${APP_HOST}}
EXPOSE_PLUGIN_DEBUGGING_PORT=${EXPOSE_PLUGIN_DEBUGGING_PORT:-5003}
PLUGIN_MAX_PACKAGE_SIZE=${PLUGIN_MAX_PACKAGE_SIZE:-52428800}
PLUGIN_STORAGE_TYPE=${PLUGIN_STORAGE_TYPE:-local}
PLUGIN_STORAGE_LOCAL_ROOT=${PLUGIN_STORAGE_LOCAL_ROOT:-/app/storage}
EXPOSE_API_PORT=${EXPOSE_API_PORT:-5001}
EXPOSE_WEB_PORT=${EXPOSE_WEB_PORT:-3000}
FORCE_VERIFYING_SIGNATURE=${FORCE_VERIFYING_SIGNATURE:-false}
COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME:-dify_origin}
COMPOSE_PROFILES=
EOF
}

write_compose_file() {
  local file="$1"

  cat >"${file}" <<'EOF'
services:
  init_permissions:
    image: busybox:latest
    command: ["sh", "-c", "mkdir -p /app/api/storage /app/api/storage/privkeys && chown -R 1001:1001 /app/api/storage"]
    volumes:
      - ./volumes/app/storage:/app/api/storage
    restart: "no"

  api:
    image: dify-origin-api:__IMAGE_TAG__
    restart: always
    env_file: [./.env]
    environment:
      MODE: api
      INNER_API_KEY_FOR_PLUGIN: ${PLUGIN_DIFY_INNER_API_KEY}
    depends_on:
      init_permissions:
        condition: service_completed_successfully
      sandbox:
        condition: service_started
      plugin_daemon:
        condition: service_started
    ports:
      - "${EXPOSE_API_PORT:-5001}:5001"
    volumes:
      - ./volumes/app/storage:/app/api/storage
    networks: [default, ssrf_proxy_network]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5001/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 60s

  worker:
    image: dify-origin-api:__IMAGE_TAG__
    restart: always
    env_file: [./.env]
    environment:
      MODE: worker
      WECOM_LONG_LINK_ENABLED: "false"
      INNER_API_KEY_FOR_PLUGIN: ${PLUGIN_DIFY_INNER_API_KEY}
      CELERY_WORKER_CONCURRENCY: ${CELERY_WORKER_AMOUNT:-4}
      CELERY_WORKER_QUEUES: api_token,dataset,dataset_summary,priority_dataset,priority_pipeline,pipeline,mail,ops_trace,app_deletion,plugin,workflow_storage,conversation,workflow,schedule_poller,schedule_executor,triggered_workflow_dispatcher,trigger_refresh_publisher,trigger_refresh_executor,retention,workflow_based_app_execution
    depends_on:
      init_permissions:
        condition: service_completed_successfully
      sandbox:
        condition: service_started
      plugin_daemon:
        condition: service_started
    volumes:
      - ./volumes/app/storage:/app/api/storage
    networks: [default, ssrf_proxy_network]

  worker_graph:
    image: dify-origin-api:__IMAGE_TAG__
    restart: always
    env_file: [./.env]
    environment:
      MODE: worker
      WECOM_LONG_LINK_ENABLED: "false"
      INNER_API_KEY_FOR_PLUGIN: ${PLUGIN_DIFY_INNER_API_KEY}
      CELERY_WORKER_CONCURRENCY: ${GRAPH_INDEX_CELERY_WORKER_CONCURRENCY:-1}
      CELERY_WORKER_QUEUES: graph_index
    depends_on:
      init_permissions:
        condition: service_completed_successfully
      sandbox:
        condition: service_started
      plugin_daemon:
        condition: service_started
    volumes:
      - ./volumes/app/storage:/app/api/storage
    networks: [default, ssrf_proxy_network]

  wecom_worker:
    image: dify-origin-api:__IMAGE_TAG__
    restart: always
    env_file: [./.env]
    environment:
      MODE: wecom_long_link
      WECOM_LONG_LINK_ENABLED: "true"
      MIGRATION_ENABLED: "false"
      INNER_API_KEY_FOR_PLUGIN: ${PLUGIN_DIFY_INNER_API_KEY}
    depends_on:
      init_permissions:
        condition: service_completed_successfully
      sandbox:
        condition: service_started
      plugin_daemon:
        condition: service_started
    volumes:
      - ./volumes/app/storage:/app/api/storage
    networks: [default, ssrf_proxy_network]
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import os,sys; p=os.getpid(); sys.exit(0 if any(x.isdigit() and int(x)!=p and 'core.wecom_long_link.worker' in open('/proc/'+x+'/cmdline','rb').read().decode(errors='ignore') for x in os.listdir('/proc')) else 1)\""]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s

  worker_beat:
    image: dify-origin-api:__IMAGE_TAG__
    restart: always
    env_file: [./.env]
    environment:
      MODE: beat
    depends_on:
      init_permissions:
        condition: service_completed_successfully
    networks: [default, ssrf_proxy_network]

  web:
    image: dify-origin-web:__IMAGE_TAG__
    restart: always
    env_file: [./.env]
    environment:
      HOSTNAME: 0.0.0.0
    ports:
      - "${EXPOSE_WEB_PORT:-3000}:3000"
    depends_on:
      api:
        condition: service_started
    networks: [default, ssrf_proxy_network]

  sandbox:
    image: langgenius/dify-sandbox:0.2.15
    restart: always
    env_file: [./.env]
    environment:
      API_KEY: ${SANDBOX_API_KEY:-dify-sandbox}
      GIN_MODE: ${SANDBOX_GIN_MODE:-release}
      WORKER_TIMEOUT: ${SANDBOX_WORKER_TIMEOUT:-15}
      ENABLE_NETWORK: ${SANDBOX_ENABLE_NETWORK:-true}
      HTTP_PROXY: ${SANDBOX_HTTP_PROXY:-http://ssrf_proxy:3128}
      HTTPS_PROXY: ${SANDBOX_HTTPS_PROXY:-http://ssrf_proxy:3128}
      SANDBOX_PORT: ${SANDBOX_PORT:-8194}
      PIP_MIRROR_URL: ${PIP_MIRROR_URL:-}
    volumes:
      - ./volumes/sandbox/dependencies:/dependencies
      - ./volumes/sandbox/conf:/conf
    networks: [ssrf_proxy_network]

  plugin_daemon:
    image: langgenius/dify-plugin-daemon:0.6.3-local
    restart: always
    env_file: [./.env]
    environment:
      DB_DATABASE: ${DB_PLUGIN_DATABASE:-dify}
      DB_SSL_MODE: ${DB_SSL_MODE:-disable}
      SERVER_PORT: ${PLUGIN_DAEMON_PORT:-5002}
      SERVER_KEY: ${PLUGIN_DAEMON_KEY}
      MAX_PLUGIN_PACKAGE_SIZE: ${PLUGIN_MAX_PACKAGE_SIZE:-52428800}
      PPROF_ENABLED: ${PLUGIN_PPROF_ENABLED:-false}
      DIFY_INNER_API_URL: ${PLUGIN_DIFY_INNER_API_URL:-http://api:5001}
      DIFY_INNER_API_KEY: ${PLUGIN_DIFY_INNER_API_KEY}
      PLUGIN_REMOTE_INSTALLING_HOST: ${PLUGIN_DEBUGGING_HOST:-0.0.0.0}
      PLUGIN_REMOTE_INSTALLING_PORT: ${PLUGIN_DEBUGGING_PORT:-5003}
      PLUGIN_WORKING_PATH: ${PLUGIN_WORKING_PATH:-/app/storage/cwd}
      FORCE_VERIFYING_SIGNATURE: ${FORCE_VERIFYING_SIGNATURE:-false}
      PYTHON_ENV_INIT_TIMEOUT: ${PLUGIN_PYTHON_ENV_INIT_TIMEOUT:-120}
      PLUGIN_MAX_EXECUTION_TIMEOUT: ${PLUGIN_MAX_EXECUTION_TIMEOUT:-600}
      PLUGIN_STDIO_BUFFER_SIZE: ${PLUGIN_STDIO_BUFFER_SIZE:-1024}
      PLUGIN_STDIO_MAX_BUFFER_SIZE: ${PLUGIN_STDIO_MAX_BUFFER_SIZE:-5242880}
      PIP_MIRROR_URL: ${PIP_MIRROR_URL:-}
      PLUGIN_STORAGE_TYPE: ${PLUGIN_STORAGE_TYPE:-local}
      PLUGIN_STORAGE_LOCAL_ROOT: ${PLUGIN_STORAGE_LOCAL_ROOT:-/app/storage}
      PLUGIN_INSTALLED_PATH: ${PLUGIN_INSTALLED_PATH:-plugin}
      PLUGIN_PACKAGE_CACHE_PATH: ${PLUGIN_PACKAGE_CACHE_PATH:-plugin_packages}
      PLUGIN_MEDIA_CACHE_PATH: ${PLUGIN_MEDIA_CACHE_PATH:-assets}
    ports:
      - "${EXPOSE_PLUGIN_DEBUGGING_PORT:-5003}:5003"
    volumes:
      - ./volumes/plugin_daemon:/app/storage
    networks: [default, ssrf_proxy_network]

  ssrf_proxy:
    image: ubuntu/squid:latest
    restart: always
    volumes:
      - ./ssrf_proxy/squid.conf.template:/etc/squid/squid.conf.template
      - ./ssrf_proxy/docker-entrypoint.sh:/docker-entrypoint-mount.sh
    entrypoint:
      - sh
      - -c
      - cp /docker-entrypoint-mount.sh /docker-entrypoint.sh && sed -i 's/\r$$//' /docker-entrypoint.sh && chmod +x /docker-entrypoint.sh && /docker-entrypoint.sh
    environment:
      HTTP_PORT: ${SSRF_HTTP_PORT:-3128}
      COREDUMP_DIR: ${SSRF_COREDUMP_DIR:-/var/spool/squid}
      SSRF_PROXY_ALLOW_PRIVATE_IPS: ${SSRF_PROXY_ALLOW_PRIVATE_IPS:-}
      SSRF_PROXY_ALLOW_PRIVATE_DOMAINS: ${SSRF_PROXY_ALLOW_PRIVATE_DOMAINS:-}
    networks: [default, ssrf_proxy_network, ragflow]

networks:
  default:
  ssrf_proxy_network:
    internal: true
  ragflow:
    external: true
    name: docker_ragflow
EOF

  sed -i "s/__IMAGE_TAG__/${IMAGE_TAG}/g" "${file}"
}

sync_remote_files() {
  local temp_dir
  temp_dir="$(mktemp -d)"
  DEPLOY_TMP_DIR="${temp_dir}"
  trap 'rm -rf "${DEPLOY_TMP_DIR:-}"' RETURN

  write_runtime_env_file "${temp_dir}/.env"
  write_compose_file "${temp_dir}/docker-compose.origin.yaml"

  ssh "${REMOTE}" "mkdir -p '${REMOTE_DIR}/docker/ssrf_proxy' '${REMOTE_DIR}/docker/volumes/sandbox/conf'"
  scp "${temp_dir}/.env" "${REMOTE}:${REMOTE_DIR}/docker/.env"
  scp "${temp_dir}/docker-compose.origin.yaml" "${REMOTE}:${REMOTE_DIR}/docker/docker-compose.origin.yaml"
  scp "${SCRIPT_DIR}/ssrf_proxy/squid.conf.template" "${REMOTE}:${REMOTE_DIR}/docker/ssrf_proxy/squid.conf.template"
  scp "${SCRIPT_DIR}/ssrf_proxy/docker-entrypoint.sh" "${REMOTE}:${REMOTE_DIR}/docker/ssrf_proxy/docker-entrypoint.sh"
  scp "${SCRIPT_DIR}/volumes/sandbox/conf/config.yaml" "${REMOTE}:${REMOTE_DIR}/docker/volumes/sandbox/conf/config.yaml"
  ssh "${REMOTE}" "chmod 600 '${REMOTE_DIR}/docker/.env'"
}

main() {
  require_cmd docker
  require_cmd ssh
  require_cmd scp
  require_cmd git

  cd "${REPO_ROOT}"
  ensure_builder

  local commit_sha
  commit_sha="$(git rev-parse --short HEAD)"

  build_image api/Dockerfile "${API_IMAGE}" "${commit_sha}"
  build_image web/Dockerfile "${WEB_IMAGE}" "${commit_sha}"

  pull_with_mirror "library/busybox:latest" "${BUSYBOX_IMAGE}"
  pull_with_mirror "langgenius/dify-sandbox:0.2.15" "${SANDBOX_IMAGE}"
  pull_with_mirror "ubuntu/squid:latest" "${SQUID_IMAGE}"
  pull_with_mirror "langgenius/dify-plugin-daemon:0.6.3-local" "${PLUGIN_IMAGE}"

  rm -f "${LOCAL_IMAGE_TAR}"
  docker save -o "${LOCAL_IMAGE_TAR}" \
    "${API_IMAGE}" "${WEB_IMAGE}" "${PLUGIN_IMAGE}" "${SANDBOX_IMAGE}" "${SQUID_IMAGE}" "${BUSYBOX_IMAGE}"

  sync_remote_files
  scp "${LOCAL_IMAGE_TAR}" "${REMOTE}:${REMOTE_IMAGE_TAR}"
  ssh "${REMOTE}" "docker load -i '${REMOTE_IMAGE_TAR}'"
  # 以一次性任务方式执行迁移，再启动应用服务。
  # 避免新版本容器在迁移前查询尚未创建的数据库字段。
  ssh "${REMOTE}" "cd '${REMOTE_DIR}/docker' && docker compose -f docker-compose.origin.yaml run --rm --no-deps -e MODE=job api upgrade-db"
  ssh "${REMOTE}" "cd '${REMOTE_DIR}/docker' && docker compose -f docker-compose.origin.yaml up -d"
  ssh "${REMOTE}" "cd '${REMOTE_DIR}/docker' && docker compose -f docker-compose.origin.yaml ps"
  ssh "${REMOTE}" "curl -fsS http://127.0.0.1:5001/health || true"
}

main "$@"
