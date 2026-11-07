#!/bin/bash
# build_libpd_final.sh - Build libpd.dll from existing sources (no git)

set -e

PROJECT_DIR="/c/pythonprojects/crudelang"
LIBPD_SRC="${PROJECT_DIR}/thrid-party/libpd"
PD_SRC="${PROJECT_DIR}/thrid-party/pure-data"    # your existing Pd source
INSTALL_DIR="${PROJECT_DIR}/libs"

mkdir -p "${INSTALL_DIR}"
cd "${LIBPD_SRC}"

# Ensure pure-data subdirectory exists and contains required source
if [ ! -f "${LIBPD_SRC}/pure-data/src/d_arithmetic.c" ]; then
    echo "Copying Pure Data source into libpd/pure-data ..."
    if [ ! -d "${PD_SRC}" ]; then
        echo "ERROR: Pure Data source not found at ${PD_SRC}"
        exit 1
    fi
    # Copy the whole pure-data directory (overwrites if needed)
    cp -r "${PD_SRC}" "${LIBPD_SRC}/pure-data"
    echo "Copy completed."
fi

# Clean previous builds
make clean 2>/dev/null || true

# Build libpd shared library (DLL) with Clang
echo "Building libpd (this may take a minute)..."
make \
    CC=clang \
    CXX=clang++ \
    ADDITIONAL_CFLAGS='-DPD_LONGINTTYPE="long long"' \
    -j$(nproc)

# Locate the produced DLL (name may be libpd.dll or pd.dll)
DLL_PATH=""
if [ -f "${LIBPD_SRC}/libs/libpd.dll" ]; then
    DLL_PATH="${LIBPD_SRC}/libs/libpd.dll"
elif [ -f "${LIBPD_SRC}/libs/pd.dll" ]; then
    DLL_PATH="${LIBPD_SRC}/libs/pd.dll"
fi

if [ -z "${DLL_PATH}" ]; then
    echo "ERROR: No DLL found in libs/ after build."
    exit 1
fi

cp -v "${DLL_PATH}" "${INSTALL_DIR}/libpd.dll"

# Copy import library if present
if [ -f "${LIBPD_SRC}/libs/libpd.lib" ]; then
    cp -v "${LIBPD_SRC}/libs/libpd.lib" "${INSTALL_DIR}/"
elif [ -f "${LIBPD_SRC}/libs/pd.lib" ]; then
    cp -v "${LIBPD_SRC}/libs/pd.lib" "${INSTALL_DIR}/"
fi

cd /c/pythonprojects/crudeprofiler/libs

# Copy all common runtime DLLs (ignore errors if some aren't found)
cp /ucrt64/bin/libwinpthread-1.dll . 2>/dev/null
cp /clang64/bin/libwinpthread-1.dll . 2>/dev/null
cp /mingw64/bin/libwinpthread-1.dll . 2>/dev/null

cp /ucrt64/bin/libc++.dll . 2>/dev/null
cp /clang64/bin/libc++.dll . 2>/dev/null
cp /clang64/bin/libc++-1.dll . 2>/dev/null

cp /ucrt64/bin/libstdc++-6.dll . 2>/dev/null
cp /mingw64/bin/libstdc++-6.dll . 2>/dev/null

cp /ucrt64/bin/libunwind.dll . 2>/dev/null
cp /clang64/bin/libunwind.dll . 2>/dev/null

# Also libgcc_s_seh (if using GCC runtime)
cp /ucrt64/bin/libgcc_s_seh-1.dll . 2>/dev/null
cp /mingw64/bin/libgcc_s_seh-1.dll . 2>/dev/nul

echo "✅ libpd.dll successfully placed in ${INSTALL_DIR}"