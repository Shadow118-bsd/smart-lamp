idf_component_register(SRCS "src/sensors.c"
                            "src/context_engine.c"
                       INCLUDE_DIRS "include"
                       REQUIRES driver freertos esp_timer)
