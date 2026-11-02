#!/bin/bash
# build_libpd_dll.sh - Build libpd DLL correctly

set -e

PROJECT_DIR="/c/pythonprojects/crudeprofiler"
LIBPD_SRC="${PROJECT_DIR}/thrid-party/libpd"
PD_SRC="${PROJECT_DIR}/thrid-party/pure-data"
INSTALL_DIR="${PROJECT_DIR}/libs"

mkdir -p "${INSTALL_DIR}"
cd "${LIBPD_SRC}"

# Copy pure-data source if missing
if [ ! -f "${LIBPD_SRC}/pure-data/src/d_arithmetic.c" ]; then
    echo "Copying Pure Data source into libpd/pure-data ..."
    cp -r "${PD_SRC}" "${LIBPD_SRC}/pure-data"
fi

# Clean previous builds
make clean 2>/dev/null || true

# Build libpd as shared library (DLL)
# Note: -DPD_LONGINTTYPE="long long" is needed for 64-bit Windows
# Do NOT put -march or -mtune inside quotes – they are separate CFLAGS
make \
    CC=clang \
    CXX=clang++ \
    ADDITIONAL_CFLAGS='-DPD_LONGINTTYPE="long long"' \
    -j$(nproc)

# Locate DLL
DLL_PATH="${LIBPD_SRC}/libs/libpd.dll"
if [ ! -f "${DLL_PATH}" ]; then
    DLL_PATH="${LIBPD_SRC}/libs/pd.dll"
fi

if [ ! -f "${DLL_PATH}" ]; then
    echo "ERROR: DLL not found after build."
    exit 1
fi

# Copy to project libs
cp -v "${DLL_PATH}" "${INSTALL_DIR}/libpd.dll"

# Copy import library if present
if [ -f "${LIBPD_SRC}/libs/libpd.lib" ]; then
    cp -v "${LIBPD_SRC}/libs/libpd.lib" "${INSTALL_DIR}/"
elif [ -f "${LIBPD_SRC}/libs/pd.lib" ]; then
    cp -v "${LIBPD_SRC}/libs/pd.lib" "${INSTALL_DIR}/"
fi

# Copy runtime dependencies from MSYS2
cd "${INSTALL_DIR}"
cp /ucrt64/bin/libwinpthread-1.dll . 2>/dev/null || true
cp /ucrt64/bin/libc++.dll . 2>/dev/null || true
cp /ucrt64/bin/libunwind.dll . 2>/dev/null || true

echo "✅ libpd.dll successfully built and placed in ${INSTALL_DIR}"