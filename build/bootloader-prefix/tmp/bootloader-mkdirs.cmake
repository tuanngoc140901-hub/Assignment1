# Distributed under the OSI-approved BSD 3-Clause License.  See accompanying
# file Copyright.txt or https://cmake.org/licensing for details.

cmake_minimum_required(VERSION 3.5)

file(MAKE_DIRECTORY
  "/home/ngoc-202414649/esp-idf/components/bootloader/subproject"
  "/home/ngoc-202414649/data/Assign1/build/bootloader"
  "/home/ngoc-202414649/data/Assign1/build/bootloader-prefix"
  "/home/ngoc-202414649/data/Assign1/build/bootloader-prefix/tmp"
  "/home/ngoc-202414649/data/Assign1/build/bootloader-prefix/src/bootloader-stamp"
  "/home/ngoc-202414649/data/Assign1/build/bootloader-prefix/src"
  "/home/ngoc-202414649/data/Assign1/build/bootloader-prefix/src/bootloader-stamp"
)

set(configSubDirs )
foreach(subDir IN LISTS configSubDirs)
    file(MAKE_DIRECTORY "/home/ngoc-202414649/data/Assign1/build/bootloader-prefix/src/bootloader-stamp/${subDir}")
endforeach()
if(cfgdir)
  file(MAKE_DIRECTORY "/home/ngoc-202414649/data/Assign1/build/bootloader-prefix/src/bootloader-stamp${cfgdir}") # cfgdir has leading slash
endif()
