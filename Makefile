CC      ?= cc
CFLAGS  ?= -O2 -Wall -Wextra -std=c11 -fPIC
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

all: $(LIB)

$(LIB): movegen.c Constants.h
	@mkdir -p $(LIBDIR)
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ movegen.c

clean:
	rm -f $(LIBDIR)/libsetupcore.dylib $(LIBDIR)/libsetupcore.so

.PHONY: all clean
