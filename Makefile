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

SRC = movegen.c eval.c search.c

$(LIB): $(SRC) Constants.h
	@mkdir -p $(LIBDIR)
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $(SRC)

clean:
	rm -f $(LIBDIR)/libsetupcore.dylib $(LIBDIR)/libsetupcore.so

.PHONY: all clean
