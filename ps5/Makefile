# PS5 side of Game Shop. Needs john-tornblom's ps5-payload-sdk (Linux / WSL):
#   PS5_PAYLOAD_SDK=/opt/ps5-payload-sdk make
# Builds the gameshop-open payload and the Game Shop PS5 app. Both are committed under gameshop/
# so the Windows build needs no SDK.
ifdef PS5_PAYLOAD_SDK
    include $(PS5_PAYLOAD_SDK)/toolchain/prospero.mk
else
    $(error PS5_PAYLOAD_SDK is undefined)
endif

OUT      := ../gameshop/payloads
CFLAGS   := -Wall -Werror -O1

all: $(OUT)/gameshop-open.elf app

$(OUT)/gameshop-open.elf: open/main.c
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -lSceSystemService -lSceUserService -o $@ $^

# ---------- the Game Shop app: the store on the PS5, and its home-screen tile ----------
# A folder Shadow Mount+ puts on the home screen when it sits in /data/homebrew/$(APP_ID)/
# (it only accepts IDs starting PPSA, CUSA, LAPY or FAKE). The payload SDK has no tools for apps;
# the native-app kit inside Forwarder Manager (https://github.com/heydemoura/ps5-forwarder-manager,
# GPL-3.0-or-later) has the linker layout, the converter that makes a fake-signed eboot.bin, and
# the clean-room libc.prx the system loader requires. Set it up once with FLEX's
# ps5-client/tools/setup-app-kit.sh (github.com/MrRobotto512/FLEX), into ~/ps5-app-kit.
# The app is built the way FLEX builds its own (see FLEX's ps5-client/Makefile, `make app`).
APP_KIT  ?= $(HOME)/ps5-app-kit
APP_ID   ?= PPSA87500
APP_NAME ?= Game Shop
APP_OUT  := ../gameshop/ps5app/$(APP_ID)
APP_TOOL := $(APP_KIT)/build/host/ps5-native-tool
BUILD    := build
APP_SRCS := app/main.c app/start.c app/heap.c app/net.c app/covers.c third_party/cJSON/cJSON.c
APP_OBJS := $(patsubst %.c,$(BUILD)/app/%.o,$(APP_SRCS))
APP_CFLAGS := -Wall -O2 -std=gnu11 -Iapp -Ithird_party/cJSON -DTITLE_ID='"$(APP_ID)"' \
              $(shell $(PS5_PAYLOAD_SDK)/bin/prospero-sdl2-config --cflags) \
              $(shell $(PKG_CONFIG) --cflags SDL2_image freetype2)
# SDL and its friends, then the system modules (the app's own libkernel), then the payload SDK's
# libc.a for what the system's C library lacks (getaddrinfo, ...).
APP_LIBS := $(filter-out -pthread -D%,$(shell $(PS5_PAYLOAD_SDK)/bin/prospero-sdl2-config --libs) -lSDL2_ttf \
              $(shell $(PKG_CONFIG) --libs --static SDL2_image freetype2) -lm -lpthread) -L$(PS5_SYSROOT)/lib
APP_SYS  := -lkernel -lSceLibcInternal -lSceNet
# System calls that may not be bound in an app (app/start.c guards them), and the heap (app/heap.c).
APP_WRAP := sceKeyboardInit sceKeyboardOpen sceKeyboardClose sceKeyboardReadState \
            sceImeDialogInit sceImeDialogGetStatus sceImeDialogGetResult sceImeDialogTerm \
            sceKernelAllocateMainDirectMemory malloc calloc realloc posix_memalign free

app: $(APP_OUT)/eboot.bin

$(BUILD)/app/%.o: %.c app/app.h
	@mkdir -p $(dir $@)
	$(CC) $(APP_CFLAGS) -c -o $@ $<

$(BUILD)/app/third_party/%.o: APP_CFLAGS += -Wno-unreachable-code-generic-assoc

$(BUILD)/app/pie.elf: $(APP_OBJS) app/symbols.map
	$(LD) -T $(APP_KIT)/tooling/native/ps5-pie.ld --version-script app/symbols.map -e _start \
	    $(addprefix --wrap=,$(APP_WRAP)) -o $@ $(APP_OBJS) --start-group $(APP_LIBS) --end-group --as-needed $(APP_SYS) -lc

# param.json may have Windows line endings in a Windows checkout; the console gets plain ones.
$(APP_OUT)/eboot.bin: $(BUILD)/app/pie.elf app/param.json ../gameshop/web/tile-icon.png $(wildcard app/assets/*)
	$(APP_TOOL) link --in $< --out $(BUILD)/app/eboot.elf --stub-dir $(PS5_SYSROOT)/lib \
	    --module-sdk 0x02000009 --companion-sdk 0x08050001 --file-name eboot.elf
	rm -rf $(APP_OUT)
	mkdir -p $(APP_OUT)/sce_module $(APP_OUT)/sce_sys $(APP_OUT)/assets
	$(APP_TOOL) self --sign --in $(BUILD)/app/eboot.elf --out $@ --magic 0x1D3D154F
	cp $(APP_KIT)/runtime/libc.prx $(APP_OUT)/sce_module/
	sed 's/\r$$//; s/@ID@/$(APP_ID)/g; s/@CONCEPT@/$(patsubst PPSA%,%,$(APP_ID))/; s/@NAME@/$(APP_NAME)/' \
	    app/param.json > $(APP_OUT)/sce_sys/param.json
	cp ../gameshop/web/tile-icon.png $(APP_OUT)/sce_sys/icon0.png
	cp app/assets/*.ttf $(APP_OUT)/assets/

clean:
	rm -rf $(BUILD) $(OUT)/gameshop-open.elf $(APP_OUT)

.PHONY: all app clean
