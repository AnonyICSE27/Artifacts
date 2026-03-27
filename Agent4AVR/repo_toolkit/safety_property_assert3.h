#ifndef SAFETY_ASSERT_H
#define SAFETY_ASSERT_H

#define SAFETY_PROPERTY_ASSERT(cond, fmt, ...) \
    do { \
        extern void _agent4avr_stderr_printf(const char *, ...); \
        _agent4avr_stderr_printf("[%s] %s:%d | %s | " fmt "\n", \
               (cond) ? "PASS" : "FAIL", \
               __FILE__, __LINE__, #cond, ##__VA_ARGS__); \
    } while (0)

#endif
