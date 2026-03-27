#include <stdio.h>
#include <stdarg.h>

void _agent4avr_stderr_printf(const char *fmt, ...) {
    va_list args;
    va_start(args, fmt);
    vfprintf(stderr, fmt, args);
    va_end(args);
}
