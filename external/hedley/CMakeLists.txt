if(NOT TARGET hedley)
    add_library(hedley INTERFACE IMPORTED GLOBAL)
    set_target_properties(hedley PROPERTIES INTERFACE_INCLUDE_DIRECTORIES "${CMAKE_CURRENT_LIST_DIR}/include")
endif()
