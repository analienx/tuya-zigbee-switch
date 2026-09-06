#include "atoi.h"

int atoi(const char *str) {
    int result = 0;
    int sign   = 1;
    int i      = 0;

    // Handle null pointer
    if (str == 0) {
        return 0;
    }

    // Skip leading whitespace
    while (str[i] == ' ' || str[i] == '\t' || str[i] == '\n' || str[i] == '\r' ||
           str[i] == '\f' || str[i] == '\v') {
        i++;
    }

    // Handle optional sign
    if (str[i] == '-') {
        sign = -1;
        i++;
    } else if (str[i] == '+') {
        i++;
    }

    // Convert digits
    while (str[i] >= '0' && str[i] <= '9') {
        result = result * 10 + (str[i] - '0');
        i++;
    }

    return result * sign;
}

/* The pinned Telink libc does not provide strstr(). Keep this implementation
 * deliberately dependency-free so common parser code can use the standard
 * interface without pulling host-libc assumptions into the firmware. */
char *strstr(const char *haystack, const char *needle) {
    if (*needle == '\0') {
        return (char *)haystack;
    }

    for (; *haystack != '\0'; haystack++) {
        const char *h = haystack;
        const char *n = needle;

        while (*h != '\0' && *n != '\0' && *h == *n) {
            h++;
            n++;
        }
        if (*n == '\0') {
            return (char *)haystack;
        }
    }

    return 0;
}
