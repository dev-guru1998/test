#!/usr/bin/env bash
set -euo pipefail

if [ -z "${ALLOW_PYARROW_OVERRIDE+x}" ]; then
  if [ -n "${PYARROW_VERSION:-}" ]; then
    ALLOW_PYARROW_OVERRIDE=1
  else
    ALLOW_PYARROW_OVERRIDE=0
  fi
fi

RPM_PACKAGES=(
  coreutils-single
  curl-minimal
  openssl
  expat
  glib2
  glibc
  python3-urllib3
  gnupg2
  sqlite-libs
  libnghttp2
  gnutls
  krb5-libs
  libarchive
  libblkid
  python3
  libcap
  libxml2
  openldap
  p11-kit
  python3-pip-wheel
  python3-setuptools-wheel
  systemd
  tar
)

echo "Python package versions:"
python - <<'PY'
import importlib.metadata as metadata
import os
import sys

packages = ("pip", "graphrag", "graphrag-llm", "litellm", "pyarrow", "cryptography", "mammoth", "openpyxl", "setuptools", "wheel")
expected = {
    "pip": os.environ.get("PIP_VERSION"),
    "graphrag": os.environ.get("GRAPHRAG_VERSION"),
    "litellm": os.environ.get("LITELLM_VERSION"),
    "pyarrow": os.environ.get("PYARROW_VERSION"),
    "cryptography": os.environ.get("CRYPTOGRAPHY_VERSION"),
    "mammoth": os.environ.get("MAMMOTH_VERSION"),
    "openpyxl": os.environ.get("OPENPYXL_VERSION"),
    "setuptools": os.environ.get("SETUPTOOLS_VERSION"),
    "wheel": os.environ.get("WHEEL_VERSION"),
}
failures = []
for package in packages:
    try:
        version = metadata.version(package)
    except metadata.PackageNotFoundError:
        version = "not-installed"
    print(f"{package}=={version}")
    expected_version = expected.get(package) or None
    if expected_version and version != expected_version:
        failures.append(f"{package}: expected {expected_version}, found {version}")

if failures:
    print("\nExpected Python package pins were not met:", file=sys.stderr)
    for failure in failures:
        print(f"- {failure}", file=sys.stderr)
    sys.exit(1)
PY

echo
if command -v rpm >/dev/null 2>&1; then
  echo "RPM package versions:"
  rpm -q "${RPM_PACKAGES[@]}" || true
else
  echo "RPM package versions: rpm is not available in this image; skipping RPM checks."
fi

echo
echo "pip dependency check:"
set +e
pip_check_output="$(python -m pip check 2>&1)"
pip_check_status=$?
set -e
printf '%s\n' "${pip_check_output}"

if [ "${pip_check_status}" -eq 0 ]; then
  echo "pip check passed."
  exit 0
fi

is_expected_pip_check_line() {
  local line="$1"
  case "${line}" in
    litellm*"has requirement "*", but you have "*)
      return 0
      ;;
    graphrag-llm*"has requirement litellm==1.82.6"*", but you have litellm "*)
      return 0
      ;;
    graphrag*"has requirement pyarrow~=22.0"*", but you have pyarrow "*)
      if [ "${ALLOW_PYARROW_OVERRIDE}" = "1" ] || [ "${ALLOW_PYARROW_OVERRIDE}" = "true" ]; then
        return 0
      fi
      ;;
  esac
  return 1
}

unexpected_lines=""
while IFS= read -r line; do
  if [ -z "${line}" ]; then
    continue
  fi
  if ! is_expected_pip_check_line "${line}"; then
    unexpected_lines="${unexpected_lines}${line}
"
  fi
done <<EOF
${pip_check_output}
EOF

if [ -n "${unexpected_lines}" ]; then
  echo
  echo "Unexpected pip dependency issues remain:" >&2
  printf '%s' "${unexpected_lines}" >&2
  exit "${pip_check_status}"
fi

echo
echo "Only expected staged security override dependency mismatches were reported."
