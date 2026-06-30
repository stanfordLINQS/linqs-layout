# Portable build for the DXF core shared library (macOS / Linux / Windows).
#
#   cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
#   cmake --build build --config Release
#
# Output lands next to this file with the name pydxf/loader.py expects per-OS:
#   macOS -> libdxfcore.dylib   Linux -> libdxfcore.so   Windows -> dxfcore.dll
#
# For a quick build without CMake use build.sh (macOS/Linux) or build.bat (MSVC).
cmake_minimum_required(VERSION 3.15)
project(dxfcore CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(dxfcore SHARED dxf_parse.cpp)

# Filename: dxfcore.dll on Windows; libdxfcore.{so,dylib} elsewhere.
if(WIN32)
  set_target_properties(dxfcore PROPERTIES PREFIX "")
else()
  set_target_properties(dxfcore PROPERTIES PREFIX "lib")
endif()

# Place the artifact beside this CMakeLists for all generators/configs, so the
# loader finds it in dxfcore/ without copying.
set_target_properties(dxfcore PROPERTIES
  RUNTIME_OUTPUT_DIRECTORY         "${CMAKE_CURRENT_SOURCE_DIR}"
  RUNTIME_OUTPUT_DIRECTORY_DEBUG   "${CMAKE_CURRENT_SOURCE_DIR}"
  RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_CURRENT_SOURCE_DIR}"
  LIBRARY_OUTPUT_DIRECTORY         "${CMAKE_CURRENT_SOURCE_DIR}"
  LIBRARY_OUTPUT_DIRECTORY_DEBUG   "${CMAKE_CURRENT_SOURCE_DIR}"
  LIBRARY_OUTPUT_DIRECTORY_RELEASE "${CMAKE_CURRENT_SOURCE_DIR}")

if(MSVC)
  target_compile_options(dxfcore PRIVATE /O2 /EHsc)
else()
  target_compile_options(dxfcore PRIVATE -O3 -funroll-loops -Wall -Wextra)
endif()
