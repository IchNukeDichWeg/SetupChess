CC      ?= cc

# `:=` not `?=`. With `?=` an inherited environment variable wins, so
# `CFLAGS=-O0 make` silently compiled with no -fPIC, -std=c11, -Wall or
# -Wextra (verified with `make -n`), and the bench oracle cannot catch it
# because the node count does not change. On Linux the lost -fPIC breaks the
# -shared link outright. Extra flags go in CFLAGS_EXTRA, which is additive.
CFLAGS  := -O2 -Wall -Wextra -std=c11 -fPIC $(CFLAGS_EXTRA)

LIBDIR   = lib
LIB      = $(LIBDIR)/libsetupcore$(SOEXT)

UNAME := $(shell uname -s)
ifeq ($(UNAME),Darwin)
SOEXT = .dylib
LDFLAGS += -dynamiclib
else
SOEXT = .so
LDFLAGS += -shared
endif

SRC   = movegen.c eval.c search.c
STAMP = $(LIBDIR)/.srchash

# CONTENT HASH, NOT MTIME. make compares mtimes at 1-SECOND resolution under
# GNU Make 3.81, which is what /usr/bin/make is on macOS, and rebuilds only on
# a strictly NEWER prerequisite. The compile takes ~0.4s, so an edit landing in
# the same second as the previous build was silently not rebuilt and the stale
# dylib was kept -- reproduced 5 of 5 times with the source 3ms newer at ns
# resolution. It never self-heals, because cengine.lib() only checks that the
# file exists. Hashing the sources removes the race rather than narrowing it.
SRCHASH := $(shell cat $(SRC) Constants.h 2>/dev/null | shasum | cut -d' ' -f1)

all:
	@mkdir -p $(LIBDIR)
	@if [ ! -f $(LIB) ] || [ "`cat $(STAMP) 2>/dev/null`" != "$(SRCHASH)" ]; then \
	    echo "$(CC) $(CFLAGS) $(LDFLAGS) -o $(LIB) $(SRC)"; \
	    $(CC) $(CFLAGS) $(LDFLAGS) -o $(LIB) $(SRC) && printf '%s' "$(SRCHASH)" > $(STAMP); \
	else \
	    echo "up to date ($(LIB))"; \
	fi

clean:
	rm -f $(LIBDIR)/libsetupcore.dylib $(LIBDIR)/libsetupcore.so $(STAMP)

.PHONY: all clean
