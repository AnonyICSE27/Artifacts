#ifndef SAFETY_PROPERTY_STATE_H
#define SAFETY_PROPERTY_STATE_H

#ifdef __cplusplus
extern "C" {
#endif

#ifdef _WIN32
    #error "Windows is not supported"
#else
    #define SAFETY_PROP_STATE_API __attribute__((visibility("default")))
#endif

SAFETY_PROP_STATE_API int _SPA_safety_property_update_state(long long value, const char* file, int lineno, const char *key_fmt, ...);
SAFETY_PROP_STATE_API int _SPA_safety_property_get_state(long long index, const char *key_fmt, ...);

#ifdef __cplusplus
}
#endif

#define SAFETY_PROPERTY_UPDATE_STATE(value, key_fmt, ...) \
    _SPA_safety_property_update_state(value, __FILE__, __LINE__, key_fmt, ##__VA_ARGS__)

#define SAFETY_PROPERTY_GET_STATE(index, key_fmt, ...) \
    _SPA_safety_property_get_state(index, key_fmt, ##__VA_ARGS__)

#endif
