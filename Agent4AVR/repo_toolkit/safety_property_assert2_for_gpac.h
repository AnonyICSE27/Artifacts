#ifndef SAFETY_ASSERT_H
#define SAFETY_ASSERT_H

/* ===== MUST be defined before any libc header ===== */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#ifndef _LARGEFILE64_SOURCE
#define _LARGEFILE64_SOURCE
#endif
#ifndef _FILE_OFFSET_BITS
#define _FILE_OFFSET_BITS 64
#endif
/* ================================================== */

#include <stdio.h>

#define SAFETY_PROPERTY_ASSERT(cond, fmt, ...) \
    do { \
        fprintf(stderr, "[%s] %s:%d | %s | " fmt "\n", \
               (cond) ? "PASS" : "FAIL", \
               __FILE__, __LINE__, #cond, ##__VA_ARGS__); \
    } while (0)

#endif
