# Subtitld's thumbnail provider for .usfx files in Windows Explorer.
#   cmake -S packaging/windows/thumbnail -B build/thumbnail -A x64
#   cmake --build build/thumbnail --config Release
cmake_minimum_required(VERSION 3.20)
project(UsfxThumbnail CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")     # no runtime DLLs to ship
add_library(UsfxThumbnail SHARED UsfxThumbnail.cpp UsfxThumbnail.def)
target_compile_definitions(UsfxThumbnail PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN)
target_link_libraries(UsfxThumbnail PRIVATE ole32 oleaut32 windowscodecs shlwapi uuid gdi32 advapi32)
