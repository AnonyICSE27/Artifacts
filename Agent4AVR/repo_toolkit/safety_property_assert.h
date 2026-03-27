#ifndef SAFETY_ASSERT_H
#define SAFETY_ASSERT_H

// #include <stdio.h>

#define SAFETY_PROPERTY_ASSERT(cond, fmt, ...) \
    do { \
        extern int printf(const char*, ...); \
        printf("[%s] %s:%d | %s | " fmt "\n", \
               (cond) ? "PASS" : "FAIL", \
               __FILE__, __LINE__, #cond, ##__VA_ARGS__); \
    } while (0)

#endif
