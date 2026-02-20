#pragma once

#include <stdint.h>

// Safety-side utility header.
//
// IMPORTANT:
// - This file is included both in panda firmware builds and in host/test builds.
// - panda/board/utils.h defines get_ts_elapsed as a non-static function in a header.
//   Defining another get_ts_elapsed here (even static inline) can trigger -Werror
//   redefinition failures depending on include order (e.g. tests/libpanda).
// - Therefore: never define get_ts_elapsed here. Only declare it. A definition is
//   expected to be provided by panda/board/utils.h (firmware/libpanda), or by the
//   consumer's build if used elsewhere.

#ifndef UNUSED
#define UNUSED(x) ((void)(x))
#endif

#ifndef MIN
#define MIN(a, b) ({ \
  __typeof__(a) _a = (a); \
  __typeof__(b) _b = (b); \
  (_a < _b) ? _a : _b; \
})
#endif

#ifndef MAX
#define MAX(a, b) ({ \
  __typeof__(a) _a = (a); \
  __typeof__(b) _b = (b); \
  (_a > _b) ? _a : _b; \
})
#endif

#ifndef CLAMP
#define CLAMP(x, low, high) ({ \
  __typeof__(x) __x = (x); \
  __typeof__(low) __low = (low); \
  __typeof__(high) __high = (high); \
  (__x > __high) ? __high : ((__x < __low) ? __low : __x); \
})
#endif

#ifndef ABS
#define ABS(a) ({ \
  __typeof__(a) _a = (a); \
  (_a > 0) ? _a : (-_a); \
})
#endif

#ifndef COMPILE_TIME_ASSERT
#define COMPILE_TIME_ASSERT(pred) ((void)sizeof(char[1 - (2 * (!(pred) ? 1 : 0))]))
#endif

// Provided by panda/board/utils.h in firmware + libpanda builds.
// If you compile safety code in an environment without panda/board/utils.h, you must
// provide a definition for get_ts_elapsed somewhere in your build.
uint32_t get_ts_elapsed(uint32_t ts, uint32_t ts_last);
